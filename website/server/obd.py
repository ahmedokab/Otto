"""OBD-II over CAN (ISO 15765-4) — request building and response decoding.

These are pure functions with tests (tests/test_obd.py). The transport —
how CAN frames get to/from the FREE-WILi — lives in sources/freewili.py.

11-bit (most cars):
  Request:  CAN ID 0x7DF (functional broadcast), data = [len, mode, pid, pad...]
  Response: CAN ID 0x7E8..0x7EF, single frame data = [len, mode+0x40, pid, A, B, ...]
  Direct to one ECU: reply ID - 8 (engine 0x7E8 -> 0x7E0)
29-bit (some makes): broadcast 0x18DB33F1, replies 0x18DAF1xx, direct 0x18DAxxF1.
"""

OBD_REQUEST_ID = 0x7DF
ECU_RESPONSE_IDS = range(0x7E8, 0x7F0)
OBD_REQUEST_ID_29 = 0x18DB33F1


def is_response_id(can_id, extended=False):
    """Is this frame an ECU answering a diagnostic request?"""
    if extended:
        return (can_id & 0xFFFFFF00) == 0x18DAF100
    return can_id in ECU_RESPONSE_IDS


def physical_id(response_id, extended=False):
    """The ID to talk to one ECU directly, given the ID it answers from."""
    if extended:
        return 0x18DA00F1 | ((response_id & 0xFF) << 8)
    return response_id - 8

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
    0x04: ("load_pct",      1, lambda d: d[0] * 100 / 255),
    0x0B: ("map_kpa",       1, lambda d: d[0]),
    0x0E: ("timing_deg",    1, lambda d: d[0] / 2 - 64),
    0x2F: ("fuel_pct",      1, lambda d: d[0] * 100 / 255),
    0x33: ("baro_kpa",      1, lambda d: d[0]),
    0x46: ("ambient_c",     1, lambda d: d[0] - 40),
    0x5C: ("oil_c",         1, lambda d: d[0] - 40),
    0x1F: ("runtime_min",   2, lambda d: (d[0] * 256 + d[1]) / 60),
    0x21: ("mil_dist_km",   2, lambda d: d[0] * 256 + d[1]),
    0x31: ("clear_dist_km", 2, lambda d: d[0] * 256 + d[1]),
    0x14: ("o2_up_v",       1, lambda d: d[0] / 200),
    0x15: ("o2_down_v",     1, lambda d: d[0] / 200),
    0x23: ("rail_kpa",      2, lambda d: (d[0] * 256 + d[1]) * 10),
    0x30: ("warmups",       1, lambda d: d[0]),
    0x34: ("lambda",        2, lambda d: (d[0] * 256 + d[1]) * 2 / 65536),
    0x3C: ("cat_c",         2, lambda d: (d[0] * 256 + d[1]) / 10 - 40),
    0x44: ("cmd_lambda",    2, lambda d: (d[0] * 256 + d[1]) * 2 / 65536),
    0x47: ("throttle_b_pct", 1, lambda d: d[0] * 100 / 255),
    0x49: ("pedal_d_pct",   1, lambda d: d[0] * 100 / 255),
    0x4A: ("pedal_e_pct",   1, lambda d: d[0] * 100 / 255),
    0x4C: ("cmd_throttle_pct", 1, lambda d: d[0] * 100 / 255),
    0x4D: ("mil_time_min",  2, lambda d: d[0] * 256 + d[1]),
    0x4E: ("clear_time_min", 2, lambda d: d[0] * 256 + d[1]),
    0x5E: ("fuel_rate_lph", 2, lambda d: (d[0] * 256 + d[1]) / 20),
}
# How often to refresh each PID (seconds). The USB link manages ~14 requests/s,
# so only the readings that change fast get a fast lane. The scheduler in
# sources/freewili.py polls whatever is most overdue.
FAST, MEDIUM, SLOW, RARE = 0.6, 2.5, 5.0, 15.0
PID_INTERVAL = {
    0x0C: FAST, 0x0D: FAST, 0x11: FAST, 0x04: FAST,
    0x05: MEDIUM, 0x42: MEDIUM, 0x06: MEDIUM, 0x07: MEDIUM, 0x0B: MEDIUM, 0x0F: MEDIUM, 0x10: MEDIUM,
    0x0E: MEDIUM, 0x49: MEDIUM, 0x4A: MEDIUM, 0x4C: MEDIUM, 0x47: MEDIUM, 0x44: MEDIUM, 0x34: MEDIUM,
    0x14: MEDIUM, 0x15: MEDIUM, 0x23: MEDIUM, 0x5E: MEDIUM,
    0x5C: SLOW, 0x3C: SLOW, 0x2F: SLOW, 0x46: SLOW, 0x1F: SLOW,
    0x33: RARE, 0x21: RARE, 0x31: RARE, 0x30: RARE, 0x4D: RARE, 0x4E: RARE,
}
MONITOR_PID = 0x01         # check-engine lamp state + stored code count
PID_INTERVAL[MONITOR_PID] = SLOW
FUEL_STATUS_PID = 0x03     # open / closed loop: is the engine still warming up?
PID_INTERVAL[FUEL_STATUS_PID] = SLOW
# Reading key -> expected refresh interval, so "stale" means "later than expected"
KEY_INTERVAL = {PIDS[p][0]: s for p, s in PID_INTERVAL.items() if p in PIDS}
# Mode 01 PIDs 0x00/0x20/0x40/0x60 answer with a bitmap of the PIDs the ECU supports.
SUPPORT_PIDS = [0x00, 0x20, 0x40, 0x60]


# ---------------------------------------------------------------------------
# READ-ONLY SAFETY POLICY
# We only ever ASK for data with the standard emissions-diagnostic services
# every scan tool uses. Nothing that clears, resets, writes, unlocks, controls
# or reflashes a module can be built or sent through this code.
# ---------------------------------------------------------------------------
SAFE_SERVICES = {
    0x01: "Show current data (live PIDs)",
    0x02: "Show freeze-frame data (readings saved when a code was set)",
    0x03: "Show stored trouble codes",
    0x06: "Show on-board test results (e.g. misfire counts per cylinder)",
    0x07: "Show pending trouble codes",
    0x09: "Vehicle information (VIN, calibration IDs)",
    0x0A: "Show permanent trouble codes",
    0x19: "UDS read DTC information (manufacturer fault memory, read-only)",
}
# UDS 0x19 sub-functions we use: 0x01 count, 0x02 list by status mask. Both only read.
SAFE_UDS_READ_DTC = {0x01, 0x02}
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


def flow_control_id(response_id, extended=False):
    return physical_id(response_id, extended)


def _is_physical_id(can_id, extended):
    if extended:
        return (can_id & 0xFFFF00FF) == 0x18DA00F1
    return can_id in FLOW_CONTROL_IDS


# Sent directly to one ECU: only reading its fault memory. UDS 0x19 01/02, or
# 0x18 (KWP2000 "read DTCs by status", the older equivalent; unassigned in UDS).
SAFE_PHYSICAL_SERVICES = {0x18, 0x19}


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


def check_tx(can_id, data, extended=False):
    """Gatekeeper for EVERY frame the hardware transmits. Raises UnsafeRequest.

    Allowed: functional broadcast (0x7DF / 0x18DB33F1), single frame,
    read-only service only. Sent directly to one ECU (0x7E0-0x7E7 /
    0x18DAxxF1), only two things: the fixed ISO-TP flow-control frame, which
    carries no service and only lets an ECU finish a long reply, and a
    single-frame "read fault memory" request (SAFE_PHYSICAL_SERVICES).
    """
    if len(data) < 2 or (data[0] >> 4) != 0 and bytes(data) != FLOW_CONTROL:
        raise UnsafeRequest("TX blocked: only single-frame requests are allowed")
    if _is_physical_id(can_id, extended):
        if bytes(data) == FLOW_CONTROL:
            return True
        if data[1] not in SAFE_PHYSICAL_SERVICES:
            raise UnsafeRequest(f"TX to 0x{can_id:X} blocked: directly to an ECU only fault-memory reads are allowed")
    elif can_id != (OBD_REQUEST_ID_29 if extended else OBD_REQUEST_ID):
        raise UnsafeRequest(f"TX to 0x{can_id:X} blocked: only OBD broadcast read-only requests are allowed")
    service = data[1]
    if service == 0x18:
        if not _is_physical_id(can_id, extended):
            raise UnsafeRequest("TX blocked: KWP 0x18 only directly to one ECU")
        return True
    if service not in SAFE_SERVICES:
        why = BLOCKED_SERVICES.get(service, "not on the read-only allowlist")
        raise UnsafeRequest(f"TX blocked: service 0x{service:02X} ({why})")
    if service == 0x19 and (len(data) < 3 or data[2] not in SAFE_UDS_READ_DTC):
        raise UnsafeRequest("TX blocked: UDS 0x19 only with read sub-functions 0x01/0x02")
    return True


def build_request(mode, *params):
    """8-byte single-frame request, e.g. build_request(0x01, 0x0C) -> 02 01 0C 00 00 00 00 00

    Refuses anything outside SAFE_SERVICES.
    """
    data = [mode, *params]
    frame = bytes([len(data)] + data + [0x00] * (7 - len(data)))
    check_tx(OBD_REQUEST_ID, frame)
    return frame


# UDS fault memory: everything the engine computer is flagging, including
# manufacturer codes that Mode 03 (emissions codes only) doesn't report.
# Status mask bits: 0 failing now, 2 pending, 3 confirmed/stored, 5 failed
# since last clear, 7 warning light requested. Skips "test not run yet" noise.
UDS_STATUS_MASK = 0xAD
UDS_READ_DTCS = (0x19, 0x02, UDS_STATUS_MASK)

# Reply service byte -> how the code is reported
DTC_LIST_REPLIES = {0x43: "stored", 0x47: "pending", 0x4A: "permanent"}


def parse_dtc_list(payload):
    """Mode 03 / 07 / 0A reply payload (starts at 0x43/0x47/0x4A) -> list of codes."""
    if len(payload) < 2 or payload[0] not in DTC_LIST_REPLIES:
        return None
    codes = []
    for i in range(payload[1]):
        j = 2 + 2 * i
        if j + 1 >= len(payload):
            break
        b1, b2 = payload[j], payload[j + 1]
        if b1 == 0 and b2 == 0:
            continue
        codes.append(decode_dtc(b1, b2))
    return codes


def uds_status_tags(status):
    """UDS DTC status byte -> plain-language tags, most important first."""
    tags = []
    if status & 0x80:
        tags.append("warning light")
    if status & 0x01:
        tags.append("active")
    if status & 0x08:
        tags.append("stored")
    if status & 0x04:
        tags.append("pending")
    if not tags and status & 0x20:
        tags.append("intermittent")
    return tags


def parse_uds_dtcs(payload):
    """UDS 0x59 0x02 reply -> list of (code, tags). Each record is 3 DTC bytes + status;
    the first two bytes are the familiar P/C/B/U code, the third is a failure-type detail."""
    if len(payload) < 3 or payload[0] != 0x59 or payload[1] != 0x02:
        return None
    out = []
    for j in range(3, len(payload) - 3, 4):
        b1, b2, status = payload[j], payload[j + 1], payload[j + 3]
        tags = uds_status_tags(status)
        if (b1 or b2) and tags:
            out.append((decode_dtc(b1, b2), tags))
    return out


def parse_supported(payload):
    """Mode 01 PID 0x00/0x20/0x40/0x60 reply payload -> set of supported PIDs."""
    if len(payload) < 6 or payload[0] != 0x41 or payload[1] not in SUPPORT_PIDS:
        return None
    base = payload[1]
    bits = int.from_bytes(payload[2:6], "big")
    return {base + n for n in range(1, 33) if bits & (1 << (32 - n))}


def parse_monitor(payload):
    """Mode 01 PID 0x01 -> (check-engine lamp on, number of stored codes)."""
    if len(payload) < 3 or payload[0] != 0x41 or payload[1] != MONITOR_PID:
        return None
    return bool(payload[2] & 0x80), payload[2] & 0x7F


# ---------------------------------------------------------------------------
# Deeper diagnostics: inspection readiness, fuel control, freeze frame, misfires
# ---------------------------------------------------------------------------
CONTINUOUS_TESTS = ["Misfire", "Fuel system", "Engine sensors"]
SPARK_TESTS = ["Catalytic converter", "Heated catalyst", "Fuel vapor (EVAP)", "Secondary air",
               "A/C refrigerant", "Oxygen sensors", "Oxygen sensor heaters", "EGR / valve timing"]
DIESEL_TESTS = ["Diesel catalyst", "NOx after-treatment", None, "Boost pressure", None,
                "Exhaust gas sensor", "Particulate filter", "EGR / valve timing"]


def parse_readiness(payload):
    """Mode 01 PID 01 bytes B-D -> [{"name", "ready"}] for each emissions self-test the car runs.
    "Not ready" means the test hasn't finished since codes were last cleared, not a fault."""
    if len(payload) < 6 or payload[0] != 0x41 or payload[1] != MONITOR_PID:
        return None
    b, c, d = payload[3], payload[4], payload[5]
    tests = [{"name": n, "ready": not b & (1 << (i + 4))} for i, n in enumerate(CONTINUOUS_TESTS) if b & (1 << i)]
    names = DIESEL_TESTS if b & 0x08 else SPARK_TESTS
    tests += [{"name": n, "ready": not d & (1 << i)} for i, n in enumerate(names) if n and c & (1 << i)]
    return tests


FUEL_STATUS = {
    1: "Warming up (open loop)",
    2: "Normal (closed loop)",
    4: "Open loop: hard acceleration or slowing down",
    8: "Open loop: fault in the fuel control system",
    16: "Closed loop, but a sensor is reporting a fault",
}


def parse_fuel_status(payload):
    """Mode 01 PID 03 -> plain-language fuel control status (bank 1)."""
    if len(payload) < 3 or payload[0] != 0x41 or payload[1] != FUEL_STATUS_PID or not payload[2]:
        return None
    return {"code": payload[2], "text": FUEL_STATUS.get(payload[2], "Unknown")}


FREEZE_PIDS = [0x0C, 0x0D, 0x05, 0x04, 0x06, 0x07, 0x0B, 0x11, 0x0F, 0x10, 0x42]


def parse_freeze(payload):
    """Mode 02 reply (42 PID frame data...) -> ("code", "P0171" | None) or (reading key, value)."""
    if len(payload) < 4 or payload[0] != 0x42:
        return None
    pid, data = payload[1], payload[3:]
    if pid == 0x02:
        return ("code", decode_dtc(data[0], data[1]) if len(data) >= 2 and (data[0] or data[1]) else None)
    if pid in PIDS:
        key, n, fn = PIDS[pid]
        if len(data) >= n:
            return (key, round(fn(data[:n]), 2))
    return None


MISFIRE_MIDS = range(0xA2, 0xAE)          # Mode 06 test IDs for cylinders 1-12
MISFIRE_SUPPORT_MID = 0xA0                # bitmap of supported test IDs A1-C0


def parse_mode06(payload):
    """Mode 06 reply. Returns ("support", {mids}) for a bitmap, or ("results", [(mid, tid, value)])."""
    if len(payload) < 2 or payload[0] != 0x46:
        return None
    mid = payload[1]
    if mid % 0x20 == 0 and len(payload) == 6:
        bits = int.from_bytes(payload[2:6], "big")
        return "support", {mid + n for n in range(1, 33) if bits & (1 << (32 - n))}
    out = []
    data = payload[1:]
    for i in range(0, len(data) - 8, 9):                 # MID TID UASID value(2) min(2) max(2)
        out.append((data[i], data[i + 1], (data[i + 3] << 8) | data[i + 4]))
    return "results", out


def misfire_counts(results):
    """Mode 06 results -> {cylinder: {"recent": EWMA over last 10 drives, "last_drive": count}}."""
    cyl = {}
    for mid, tid, value in results:
        if mid in MISFIRE_MIDS:
            entry = cyl.setdefault(mid - 0xA1, {})
            if tid == 0x0B:
                entry["recent"] = value
            elif tid == 0x0C:
                entry["last_drive"] = value
    return cyl


def parse_vin(payload):
    """Mode 09 PID 02 reply payload (49 02 01 + 17 ASCII chars) -> VIN or None."""
    if len(payload) < 3 or payload[0] != 0x49 or payload[1] != 0x02:
        return None
    text = bytes(payload[3:]).decode("ascii", "ignore").strip("\x00 ").upper()
    vin = text[-17:]
    return vin if len(vin) == 17 and vin.isalnum() else None


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
    return parse_dtc_list(payload)
