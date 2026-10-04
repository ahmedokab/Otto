"""Bench ECU: a fake engine computer on Neptune channel B, for testing without a car.

Wire Neptune channel A to channel B (DB15 pin 6 <-> 4, 14 <-> 12), turn
termination ON for both channels, and start the server with
FREEWILI_BENCH_ECU=<scenario> (healthy, lean, misfire, overheat, low_voltage,
thermostat). Otto then polls channel A exactly as it would a car, and this
class answers on channel B with simulator values. Everything real except the
car: USB, firmware, Neptune, CAN wiring, ISO-TP.

NEVER connect channel B to a car while this is on: it answers as ECU 0x7E8.
"""
from .. import obd
from .simulator import SimulatorSource

ECU_ID = 0x7E8
REFRESH_S = 0.2


class BenchEcu:
    def __init__(self, scenario="healthy"):
        self.sim = SimulatorSource(scenario)
        self.sim.start()
        self.scenario = scenario
        self._frame = None
        self._frame_t = 0.0
        self._pending_cf = []          # consecutive frames held until flow control arrives

    def handle(self, can_id, data, now):
        """One frame seen on channel B -> list of (can_id, data) to transmit there."""
        if can_id == obd.flow_control_id(ECU_ID) and bytes(data[:1]) == b"\x30":
            frames, self._pending_cf = self._pending_cf, []
            return [(ECU_ID, f) for f in frames]
        if can_id != obd.OBD_REQUEST_ID or len(data) < 2 or data[0] >> 4 != 0:
            return []
        mode = data[1]
        if mode == 0x01 and len(data) >= 3:
            value = self._state(now)["readings"].get(_PID_KEYS.get(data[2]))
            if value is None:
                return []              # like a real ECU: unsupported PID -> no reply
            return [(ECU_ID, _single(obd.encode_pid(data[2], value)))]
        if mode == 0x03:
            codes = self._state(now).get("trouble_codes") or []
            payload = bytes([0x43, len(codes)]) + b"".join(bytes(obd.encode_dtc(c)) for c in codes)
            if len(payload) <= 7:
                return [(ECU_ID, _single(payload))]
            first, self._pending_cf = _multi(payload)
            return [(ECU_ID, first)]
        return []

    def _state(self, now):
        if self._frame is None or now - self._frame_t >= REFRESH_S:
            self._frame, self._frame_t = {"readings": self.sim.values(now), "trouble_codes": sorted(self.sim.latched)}, now
        return self._frame


_PID_KEYS = {pid: key for pid, (key, _, _) in obd.PIDS.items()}


def _single(payload):
    return (bytes([len(payload)]) + payload).ljust(8, b"\x00")


def _multi(payload):
    """ISO-TP: first frame + the consecutive frames that follow flow control."""
    first = bytes([0x10 | (len(payload) >> 8), len(payload) & 0xFF]) + payload[:6]
    rest, cfs, sn = payload[6:], [], 1
    while rest:
        cfs.append((bytes([0x20 | sn]) + rest[:7]).ljust(8, b"\x00"))
        rest, sn = rest[7:], (sn + 1) & 0x0F
    return first, cfs
