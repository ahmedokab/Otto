"""OBD-II over CAN (ISO 15765-4, 11-bit IDs) — request building and response decoding.

These are pure functions with tests (tests/test_obd.py). The transport —
how CAN frames get to/from the FREE-WILi — lives in sources/freewili.py.

Request:  CAN ID 0x7DF (functional broadcast), data = [len, mode, pid, pad...]
Response: CAN ID 0x7E8..0x7EF, single frame data = [len, mode+0x40, pid, A, B, ...]
"""

OBD_REQUEST_ID = 0x7DF
ECU_RESPONSE_IDS = range(0x7E8, 0x7F0)

# pid: (state key, number of data bytes, formula)
PIDS = {
    0x05: ("coolant_c",     1, lambda d: d[0] - 40),
    0x06: ("stft_pct",      1, lambda d: d[0] * 100 / 128 - 100),
    0x07: ("ltft_pct",      1, lambda d: d[0] * 100 / 128 - 100),
    0x0C: ("rpm",           2, lambda d: (d[0] * 256 + d[1]) / 4),
    0x0D: ("speed_kph",     1, lambda d: d[0]),
    0x0F: ("intake_c",      1, lambda d: d[0] - 40),
    0x10: ("maf_gs",        2, lambda d: (d[0] * 256 + d[1]) / 100),
    0x11: ("throttle_pct",  1, lambda d: d[0] * 100 / 255),
    0x42: ("ecu_voltage_v", 2, lambda d: (d[0] * 256 + d[1]) / 1000),
}
# Poll the important ones more often in your scheduler.
FAST_PIDS = [0x0C, 0x0D, 0x11]
SLOW_PIDS = [0x05, 0x42, 0x06, 0x07, 0x10, 0x0F]


# ---------------------------------------------------------------------------
# READ-ONLY SAFETY POLICY
# We only ever ASK for data with the standard emissions-diagnostic services
# every scan tool uses. Nothing that clears, resets, writes, unlocks, controls
# or reflashes a module can be built or sent through this code.
# ---------------------------------------------------------------------------
SAFE_SERVICES = {
    0x01: "Show current data (live PIDs)",
    0x03: "Show stored trouble codes",
    0x07: "Show pending trouble codes",
    0x09: "Vehicle information (VIN, calibration IDs)",
}
BLOCKED_SERVICES = {
    0x04: "Clear trouble codes / reset readiness monitors",
    0x08: "Control on-board system or component",
    0x10: "UDS diagnostic session control",
    0x11: "UDS ECU reset",
    0x14: "UDS clear diagnostic information",
    0x27: "UDS security access (unlock)",
    0x28: "UDS communication control",
    0x2E: "UDS write data by identifier",
    0x2F: "UDS input/output control",
    0x31: "UDS routine control",
    0x34: "UDS request download (reflash)",
    0x35: "UDS request upload",
    0x36: "UDS transfer data",
    0x37: "UDS transfer exit",
    0x3D: "UDS write memory by address",
    0x85: "UDS control DTC setting",
}


class UnsafeRequest(ValueError):
    pass


def check_tx(can_id, data):
    """Gatekeeper for EVERY frame the hardware transmits. Raises UnsafeRequest.

    Allowed: functional broadcast 0x7DF, single frame, read-only service only.
    """
    if can_id != OBD_REQUEST_ID:
        raise UnsafeRequest(f"TX to 0x{can_id:X} blocked: only 0x7DF read-only requests are allowed")
    if len(data) < 2 or (data[0] >> 4) != 0:
        raise UnsafeRequest("TX blocked: only single-frame requests are allowed")
    service = data[1]
    if service not in SAFE_SERVICES:
        why = BLOCKED_SERVICES.get(service, "not on the read-only allowlist")
        raise UnsafeRequest(f"TX blocked: service 0x{service:02X} ({why})")
    return True


def build_request(mode, pid=None, pad=0x00):
    """8-byte single-frame request, e.g. build_request(0x01, 0x0C) -> 02 01 0C 00 00 00 00 00

    Refuses anything outside SAFE_SERVICES.
    """
    data = [mode] + ([pid] if pid is not None else [])
    frame = bytes([len(data)] + data + [pad] * (7 - len(data)))
    check_tx(OBD_REQUEST_ID, frame)
    return frame


def parse_mode01(data):
    """Decode one single-frame Mode 01 response. Returns (key, value) or None."""
    if len(data) < 4 or (data[0] >> 4) != 0:      # not a single frame
        return None
    if data[1] != 0x41:
        return None
    pid = data[2]
    if pid not in PIDS:
        return None
    key, n, fn = PIDS[pid]
    payload = data[3:3 + n]
    if len(payload) < n:
        return None
    return key, round(fn(payload), 2)


def decode_dtc(b1, b2):
    """Two raw bytes -> 'P0171' style code."""
    letter = "PCBU"[b1 >> 6]
    return f"{letter}{(b1 >> 4) & 0x3}{b1 & 0xF:X}{b2 >> 4:X}{b2 & 0xF:X}"


def parse_mode03(payload):
    """Decode a Mode 03 response payload that starts at the service byte (0x43).

    On CAN the byte after 0x43 is the number of codes, then 2 bytes per code.
    More than 2 codes won't fit in a single frame — that needs ISO-TP
    multi-frame reassembly first (see the TODO in sources/freewili.py).
    """
    if len(payload) < 2 or payload[0] != 0x43:
        return None
    count = payload[1]
    codes = []
    for i in range(count):
        j = 2 + 2 * i
        if j + 1 >= len(payload):
            break
        b1, b2 = payload[j], payload[j + 1]
        if b1 == 0 and b2 == 0:
            continue
        codes.append(decode_dtc(b1, b2))
    return codes
