"""Run: python -m pytest -q   (these numbers match the team's agreed example payload)"""
from server import obd
from server.sources.freewili import FreeWiliSource


def test_rpm():          # 41 0C 1A F8 -> 1726 rpm
    assert obd.parse_mode01(bytes.fromhex("04410C1AF8000000")) == ("rpm", 1726)

def test_coolant():      # 0x81 = 129 - 40 = 89 C
    assert obd.parse_mode01(bytes.fromhex("0341058100000000")) == ("coolant_c", 89)

def test_voltage():      # 0x35E8 = 13800 mV
    assert obd.parse_mode01(bytes.fromhex("04414235E8000000")) == ("ecu_voltage_v", 13.8)

def test_fuel_trim():    # 0x80 = 0%, 0x96 = +17.19%
    assert obd.parse_mode01(bytes.fromhex("0341078000000000")) == ("ltft_pct", 0)
    assert obd.parse_mode01(bytes.fromhex("0341079600000000")) == ("ltft_pct", 17.19)

def test_rejects_garbage():
    assert obd.parse_mode01(bytes.fromhex("10144902013144")) is None   # multi-frame first frame
    assert obd.parse_mode01(bytes.fromhex("037F0112")) is None         # negative response

def test_request():
    assert obd.build_request(0x01, 0x0C) == bytes.fromhex("02010C0000000000")
    assert obd.build_request(0x03) == bytes.fromhex("0103000000000000")

def test_dtc_decode():
    assert obd.decode_dtc(0x01, 0x71) == "P0171"
    assert obd.decode_dtc(0x03, 0x00) == "P0300"
    assert obd.decode_dtc(0xC1, 0x00) == "U0100"
    assert obd.parse_mode03(bytes.fromhex("4302017103000000")) == ["P0171", "P0300"]

def test_live_ingest_can():
    src = FreeWiliSource()
    assert src.ingest_can(0x7E8, bytes.fromhex("04410C1AF8000000"), 100.0) == {"rpm": 1726}
    assert src.ingest_can(0x7E8, bytes.fromhex("0643020171030000"), 100.1) == {"trouble_codes": ["P0171", "P0300"]}
    assert src.ingest_can(0x123, bytes.fromhex("04410C1AF8000000"), 100.2) is None
    f = src.poll(100.3)
    assert f["readings"] == {"rpm": 1726} and f["trouble_codes"] == ["P0171", "P0300"]


import pytest

def test_read_only_allows_scan_tool_requests():
    for frame in (obd.build_request(0x01, 0x0C), obd.build_request(0x03), obd.build_request(0x07), obd.build_request(0x09, 0x02)):
        assert obd.check_tx(0x7DF, frame)

@pytest.mark.parametrize("service", [0x04, 0x08, 0x10, 0x11, 0x14, 0x27, 0x2E, 0x2F, 0x31, 0x34, 0x3D, 0x85])
def test_read_only_blocks_writes_and_resets(service):
    with pytest.raises(obd.UnsafeRequest):
        obd.build_request(service)

def test_read_only_blocks_physical_addressing_and_multiframe():
    with pytest.raises(obd.UnsafeRequest):
        obd.check_tx(0x7E0, obd.build_request(0x01, 0x0C))      # direct to engine ECU
    with pytest.raises(obd.UnsafeRequest):
        obd.check_tx(0x7DF, bytes.fromhex("1014012345678900"))  # multi-frame payload

def test_live_send_goes_through_guard():
    with pytest.raises(obd.UnsafeRequest):
        FreeWiliSource().send_can(0x7DF, bytes.fromhex("0104000000000000"))   # clear codes
