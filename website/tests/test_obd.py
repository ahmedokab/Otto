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


# ---- ISO-TP + live FREE-WILi loop -------------------------------------------
from types import SimpleNamespace
import threading
import time

from result import Ok

FF_3_CODES = bytes.fromhex("1008430301710300")   # first frame: 8-byte Mode 03 reply
CF_3_CODES = bytes.fromhex("2104200000000000")   # consecutive frame: rest of it

def test_flow_control_is_the_only_physical_frame_allowed():
    assert obd.check_tx(0x7E0, obd.FLOW_CONTROL)
    with pytest.raises(obd.UnsafeRequest):
        obd.check_tx(0x7E0, bytes.fromhex("3000050000000000"))   # anything but the fixed frame
    with pytest.raises(obd.UnsafeRequest):
        obd.check_tx(0x7E8, obd.FLOW_CONTROL)                    # not an ECU request ID

def test_isotp_reassembles_three_codes():
    a = obd.IsoTpAssembler()
    assert a.feed(0x7E8, FF_3_CODES) == (None, True)
    payload, fc = a.feed(0x7E8, CF_3_CODES)
    assert not fc and obd.parse_mode03(payload) == ["P0171", "P0300", "P0420"]

def test_isotp_drops_out_of_order_frames():
    a = obd.IsoTpAssembler()
    a.feed(0x7E8, FF_3_CODES)
    assert a.feed(0x7E8, bytes.fromhex("2204200000000000")) == (None, False)

def test_codes_merge_across_ecus():
    src = FreeWiliSource()
    src.ingest_can(0x7E8, bytes.fromhex("0443010171000000"), 1.0)
    src.ingest_can(0x7E9, bytes.fromhex("0243000000000000"), 1.1)   # transmission: no codes
    assert src.poll(1.2)["trouble_codes"] == ["P0171"]


class FakeFreeWili:
    """Answers like a car would, through the freewili package's API shape."""
    def __init__(self):
        self.sent, self.events, self.cb = [], [], None

    def can_transmit(self, channel, can_id, data, is_extended, is_fd):
        self.sent.append((can_id, bytes(data)))
        if can_id == 0x7DF and data[1:3] == b"\x01\x0C":
            self._reply(bytes.fromhex("04410C1AF8000000"))
        elif can_id == 0x7DF and data[1] == 0x03:
            self._reply(FF_3_CODES)
        elif can_id == 0x7E0 and bytes(data) == obd.FLOW_CONTROL:
            self._reply(CF_3_CODES)
        return Ok("Ok")

    def _reply(self, data):
        self.events.append(SimpleNamespace(arb_id=0x7E8, is_extended=False, data=data))

    def process_events(self):
        while self.events:
            self.cb(SimpleNamespace(name="CANRX0"), None, self.events.pop(0))
        time.sleep(0.001)

def test_live_loop_polls_car_and_reassembles_codes():
    src, fake = FreeWiliSource(), FakeFreeWili()
    fake.cb, src._fw = src._on_event, fake
    t = threading.Thread(target=src._poll_loop, daemon=True)
    t.start()
    time.sleep(1.5)            # discovery + four code checks time out first on this fake
    src._stop.set()
    t.join(1)
    assert all(obd.check_tx(i, d, i > 0x7FF) for i, d in fake.sent)   # everything sent was allowed
    assert (0x7E0, obd.FLOW_CONTROL) in fake.sent
    f = src.poll(time.time())
    assert f["readings"]["rpm"] == 1726
    assert f["trouble_codes"] == ["P0171", "P0300", "P0420"]
    assert "mil" not in f          # this fake car never reports its lamp (PID 01): don't guess


# ---- bench ECU (fake car on Neptune channel B) ------------------------------
from server.sources.bench_ecu import BenchEcu

@pytest.mark.parametrize("pid,value", [(0x0C, 1726), (0x05, 89), (0x42, 13.8), (0x07, 17.19), (0x10, 11.4), (0x0D, 38)])
def test_encode_decode_roundtrip(pid, value):
    payload = obd.encode_pid(pid, value)
    key, decoded = obd.parse_mode01(bytes([len(payload)]) + payload)
    assert key == obd.PIDS[pid][0] and decoded == pytest.approx(value, abs=0.5)

@pytest.mark.parametrize("code", ["P0171", "P0300", "P0420", "U0100", "P2187", "C1234"])
def test_dtc_roundtrip(code):
    assert obd.decode_dtc(*obd.encode_dtc(code)) == code

FIXED = {"readings": {"rpm": 1726, "coolant_c": 89, "maf_gs": None}, "trouble_codes": ["P0171", "P0300", "P0420"]}

def test_bench_ecu_answers_like_a_car():
    ecu = BenchEcu("lean")
    ecu._state = lambda now: FIXED
    [(cid, rpm)] = ecu.handle(0x7DF, obd.build_request(0x01, 0x0C), 0)
    assert cid == 0x7E8 and obd.parse_mode01(rpm) == ("rpm", 1726)
    assert ecu.handle(0x7DF, obd.build_request(0x01, 0x10), 0) == []        # unsupported -> silence
    [(_, first)] = ecu.handle(0x7DF, obd.build_request(0x03), 0)             # 3 codes: multi-frame
    a = obd.IsoTpAssembler()
    assert a.feed(0x7E8, first) == (None, True)
    [(_, cf)] = ecu.handle(0x7E0, obd.FLOW_CONTROL, 0)
    payload, _ = a.feed(0x7E8, cf)
    assert obd.parse_mode03(payload) == ["P0171", "P0300", "P0420"]


class LoopbackFreeWili(FakeFreeWili):
    """Neptune channel A wired to channel B: a frame sent on one arrives on the other."""
    def can_transmit(self, channel, can_id, data, is_extended, is_fd):
        self.sent.append((channel, can_id, bytes(data)))
        self.events.append((f"CANRX{1 - channel}", SimpleNamespace(arb_id=can_id, is_extended=False, data=bytes(data))))
        return Ok("Ok")

    def process_events(self):
        while self.events:
            name, data = self.events.pop(0)
            self.cb(SimpleNamespace(name=name), None, data)
        time.sleep(0.001)

def test_bench_loop_end_to_end():
    src, fake = FreeWiliSource(), LoopbackFreeWili()
    src.bench = BenchEcu("lean")
    src.bench._state = lambda now: FIXED
    fake.cb, src._fw = src._on_event, fake
    t = threading.Thread(target=src._poll_loop, daemon=True)
    t.start()
    time.sleep(1.5)
    src._stop.set()
    t.join(1)
    car_side = [(i, d) for ch, i, d in fake.sent if ch == 0]
    assert car_side and all(obd.check_tx(i, d, i > 0x7FF) for i, d in car_side)   # Otto's side stayed read-only
    assert all(i == 0x7E8 for ch, i, d in fake.sent if ch == 1)          # fake car only answered
    f = src.poll(time.time())
    assert f["readings"] == {"rpm": 1726, "coolant_c": 89}
    assert f["trouble_codes"] == ["P0171", "P0300", "P0420"]
    assert src.status(time.time())[1].startswith("BENCH")


# ---- more code checks, supported PIDs, VIN -------------------------------------
from server import vin as vin_decoder
from server.state import State

def test_pending_and_permanent_lists():
    assert obd.parse_dtc_list(bytes.fromhex("47010301")) == ["P0301"]
    assert obd.parse_dtc_list(bytes.fromhex("4A00")) == []
    assert obd.parse_dtc_list(bytes.fromhex("4100")) is None

def test_uds_fault_memory():
    # 59 02 mask | P2101 ftb 00 status 0x89 (warning light + stored + failing) | P0300 status 0x50 (not run: skipped)
    payload = bytes.fromhex("5902FF" + "21010089" + "03000050" + "05630024")
    assert obd.parse_uds_dtcs(payload) == [("P2101", ["warning light", "active", "stored"]),
                                           ("P0563", ["pending"])]

def test_uds_read_is_the_only_uds_allowed():
    assert obd.check_tx(0x7DF, obd.build_request(*obd.UDS_READ_DTCS))
    for sub in (0x04, 0x06, 0x14):                       # snapshot/extended reads: not on our list
        with pytest.raises(obd.UnsafeRequest):
            obd.build_request(0x19, sub)
    with pytest.raises(obd.UnsafeRequest):
        obd.build_request(0x14, 0xFF, 0xFF, 0xFF)       # clear diagnostic information

def test_supported_pid_bitmap():
    # 0xBE1FA813: the classic example bitmap for PIDs 01-20
    assert obd.parse_supported(bytes.fromhex("4100BE1FA813")) == {
        0x01, 0x03, 0x04, 0x05, 0x06, 0x07, 0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x11, 0x13, 0x15, 0x1C, 0x1F, 0x20}
    assert obd.parse_monitor(bytes.fromhex("410183076500")) == (True, 3)

VIN = "3VWJ17AT9KM600001"

def test_vin_parse_and_offline_decode():
    payload = bytes.fromhex("490201") + VIN.encode()
    assert obd.parse_vin(payload) == VIN
    info = vin_decoder.decode_offline(VIN)
    assert (info["make"], info["model"], info["year"]) == ("Volkswagen", "Beetle", 2019)
    assert vin_decoder.vehicle_name(info) == "Volkswagen Beetle 2019"

def test_vin_multiframe_fills_in_vehicle(monkeypatch):
    monkeypatch.setattr(vin_decoder, "LOOKUP_ONLINE", False)
    src = FreeWiliSource()
    payload = bytes.fromhex("490201") + VIN.encode()           # 20 bytes -> 3 frames
    src.ingest_can(0x7E8, bytes([0x10, len(payload)]) + payload[:6], 1.0)
    src.ingest_can(0x7E8, bytes([0x21]) + payload[6:13], 1.1)
    assert src.ingest_can(0x7E8, bytes([0x22]) + payload[13:20], 1.2) == {"vin": VIN}
    state = State()
    state.apply(src.poll(1.3), 1.3)
    assert state.vin == VIN and state.vehicle == "Volkswagen Beetle 2019"


class EpcCar(FakeFreeWili):
    """Like the team's Beetle: no stored OBD code, but the fault memory has one
    with the warning light on (what an EPC light looks like)."""
    REPLIES = {
        b"\x01\x00": "064100" + "98180000",            # supports 01, 04, 05, 0C, 0D only
        b"\x01\x0C": "04410C0BB8",
        b"\x03": "024300",
        b"\x07": "024700",
        b"\x0A": "024A00",
        b"\x19\x02": "075902FF21010089",
    }
    def can_transmit(self, channel, can_id, data, is_extended, is_fd):
        self.sent.append((can_id, bytes(data)))
        n = data[0]
        if data[1] == 0x19 and can_id != 0x7E0:
            return Ok("Ok")            # like the real Beetle: fault memory only when asked directly
        for req, hexreply in self.REPLIES.items():
            if bytes(data[1:1 + n]).startswith(req):
                self._reply(bytes.fromhex(hexreply).ljust(8, b"\x00"))
                break
        return Ok("Ok")

def test_epc_style_fault_reaches_the_dashboard():
    src, fake = FreeWiliSource(), EpcCar()
    fake.cb, src._fw = src._on_event, fake
    t = threading.Thread(target=src._poll_loop, daemon=True)
    t.start()
    time.sleep(1.5)
    src._stop.set()
    t.join(1)
    assert all(obd.check_tx(i, d, i > 0x7FF) for i, d in fake.sent)
    state = State()
    state.reset("live")
    state.apply(src.poll(time.time()), time.time())
    assert state.codes == ["P2101"]
    assert state.code_status == {"P2101": ["warning light", "active", "stored"]}
    assert state.code_checks == ["manufacturer", "pending", "permanent", "stored"]
    assert state.readings["rpm"] == 750
    assert "maf_gs" not in state.supported and "rpm" in state.supported   # unsupported tiles get hidden
    assert (0x7E0, obd.build_request(*obd.UDS_READ_DTCS)) in fake.sent   # asked the engine ECU directly


def test_physical_requests_only_read_fault_memory():
    assert obd.check_tx(0x7E0, obd.build_request(*obd.UDS_READ_DTCS))
    assert obd.check_tx(0x7E0, bytes.fromhex("041802FF00000000"))         # KWP read DTCs by status
    for bad in ("02010C0000000000", "0322F19000000000", "0414FFFFFF000000", "0211010000000000"):
        with pytest.raises(obd.UnsafeRequest):
            obd.check_tx(0x7E0, bytes.fromhex(bad))     # live data, read-by-ID, clear, reset
    with pytest.raises(obd.UnsafeRequest):
        obd.check_tx(0x7DF, bytes.fromhex("041802FF00000000"))         # KWP only directly

def test_29_bit_ids():
    assert obd.check_tx(obd.OBD_REQUEST_ID_29, obd.build_request(0x01, 0x0C), True)
    assert obd.check_tx(0x18DA10F1, obd.FLOW_CONTROL, True)
    assert obd.check_tx(0x18DA10F1, obd.build_request(*obd.UDS_READ_DTCS), True)
    with pytest.raises(obd.UnsafeRequest):
        obd.check_tx(0x18DA10F1, obd.build_request(0x01, 0x0C), True)
    with pytest.raises(obd.UnsafeRequest):
        obd.check_tx(0x18DB33F1, obd.build_request(0x01, 0x0C), False)   # 29-bit ID sent as 11-bit
    assert obd.is_response_id(0x18DAF110, True) and not obd.is_response_id(0x18DAF110, False)
    assert obd.physical_id(0x18DAF110, True) == 0x18DA10F1 and obd.physical_id(0x7E8) == 0x7E0


class Car29(FakeFreeWili):
    """A car that only answers 29-bit requests (ECU address 0x10)."""
    def can_transmit(self, channel, can_id, data, is_extended, is_fd):
        self.sent.append((can_id, bytes(data)))
        if is_extended and can_id == obd.OBD_REQUEST_ID_29 and data[1:3] == b"\x01\x00":
            self._reply29(bytes.fromhex("0641000018000000"))     # supports 0C, 0D
        elif is_extended and data[1:3] == b"\x01\x0C":
            self._reply29(bytes.fromhex("04410C1AF8000000"))
        return Ok("Ok")

    def _reply29(self, data):
        self.events.append(SimpleNamespace(arb_id=0x18DAF110, is_extended=True, data=data))

def test_switches_to_29_bit_when_car_needs_it():
    src, fake = FreeWiliSource(), Car29()
    fake.cb, src._fw = src._on_event, fake
    t = threading.Thread(target=src._poll_loop, daemon=True)
    t.start()
    time.sleep(1.0)
    src._stop.set()
    t.join(1)
    assert src.extended and src.ecus == {0x18DAF110}
    f = src.poll(time.time())
    assert f["readings"]["rpm"] == 1726 and f["supported"] == ["rpm", "speed_kph"]
    assert (0x18DA10F1, obd.build_request(*obd.UDS_READ_DTCS)) in fake.sent


def test_every_decoded_reading_reaches_the_dashboard():
    import re
    from pathlib import Path
    from server.state import READINGS
    js = (Path(__file__).resolve().parents[1] / "web" / "app.js").read_text(encoding="utf-8")
    for pid, (key, _, _) in obd.PIDS.items():
        assert key in READINGS, f"state drops {key}"
        assert re.search(r"\n  " + key + r":", js), f"dashboard has no tile for {key}"
        assert pid in obd.PID_INTERVAL, f"PID 0x{pid:02X} is never polled"
