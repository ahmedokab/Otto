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


# ---------------------------------------------------------------------------
# ISO-TP multi-frame replies (ISO 15765-2). A reply longer than 7 bytes —
# e.g. Mode 03 with 3+ codes — starts with a First Frame (1N LL ...). The ECU
# then waits (~75 ms) for Flow Control "30 00 00" on its physical request ID
# (reply ID - 8) before sending Consecutive Frames (2N ...).
# ---------------------------------------------------------------------------
FLOW_CONTROL = bytes([0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])  # CTS, no block limit, no gap
FLOW_CONTROL_IDS = range(0x7E0, 0x7E8)


def flow_control_id(response_id):
    return response_id - 8


class IsoTpAssembler:
    """Turns ECU frames into complete payloads (starting at the service byte).

    feed() returns (payload or None, send_flow_control). One reassembly in
    flight per ECU response ID.
    """

    def __init__(self):
        self._rx = {}

    def feed(self, can_id, data):
        if not data:
            return None, False
        kind = data[0] >> 4
        if kind == 0:                                   # single frame
            n = data[0] & 0x0F
            if n == 0 or len(data) < 1 + n:
                return None, False
            return bytes(data[1:1 + n]), False
        if kind == 1 and len(data) >= 2:                # first frame
            total = ((data[0] & 0x0F) << 8) | data[1]
            self._rx[can_id] = {"total": total, "buf": bytearray(data[2:8]), "sn": 1}
            return None, True
        if kind == 2:                                   # consecutive frame
            rx = self._rx.get(can_id)
            if rx is None:
                return None, False
            if (data[0] & 0x0F) != rx["sn"]:            # lost a frame: drop this message
                del self._rx[can_id]
                return None, False
            rx["buf"] += data[1:8]
            rx["sn"] = (rx["sn"] + 1) & 0x0F
            if len(rx["buf"]) >= rx["total"]:
                del self._rx[can_id]
                return bytes(rx["buf"][:rx["total"]]), False
        return None, False


def check_tx(can_id, data):
    """Gatekeeper for EVERY frame the hardware transmits. Raises UnsafeRequest.

    Allowed: functional broadcast 0x7DF, single frame, read-only service only.
    One exception: the fixed ISO-TP flow-control frame, which carries no
    service byte and only tells an ECU "send the rest of your reply".
    """
    if can_id in FLOW_CONTROL_IDS and bytes(data) == FLOW_CONTROL:
        return True
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


# Inverse of PIDS, for the bench ECU (sources/bench_ecu.py): value -> data bytes.
ENCODERS = {
    0x05: lambda v: [v + 40],
    0x06: lambda v: [(v + 100) * 128 / 100],
    0x07: lambda v: [(v + 100) * 128 / 100],
    0x0C: lambda v: _u16(v * 4),
    0x0D: lambda v: [v],
    0x0F: lambda v: [v + 40],
    0x10: lambda v: _u16(v * 100),
    0x11: lambda v: [v * 255 / 100],
    0x42: lambda v: _u16(v * 1000),
}


def _u16(x):
    x = max(0, min(0xFFFF, round(x)))
    return [x >> 8, x & 0xFF]


def encode_pid(pid, value):
    """Mode 01 payload for one reading, e.g. encode_pid(0x0C, 1726) -> 41 0C 1A F8"""
    return bytes([0x41, pid] + [max(0, min(0xFF, round(b))) for b in ENCODERS[pid](value)])


def encode_dtc(code):
    """'P0171' -> (0x01, 0x71). Inverse of decode_dtc."""
    n = int(code[1:], 16)
    return ("PCBU".index(code[0]) << 6) | ((n >> 8) & 0x3F), n & 0xFF


def decode_dtc(b1, b2):
    """Two raw bytes -> 'P0171' style code."""
    letter = "PCBU"[b1 >> 6]
    return f"{letter}{(b1 >> 4) & 0x3}{b1 & 0xF:X}{b2 >> 4:X}{b2 & 0xF:X}"


def parse_mode03(payload):
    """Decode a Mode 03 response payload that starts at the service byte (0x43).

    On CAN the byte after 0x43 is the number of codes, then 2 bytes per code.
    More than 2 codes won't fit in a single frame — IsoTpAssembler
    reassembles those first.
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
