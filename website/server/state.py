"""Shared vehicle state: latest readings, freshness, history, codes, markers.

Every data source (simulator, replay, FREE-WILi) produces FRAMES in this shape:

    {
      "t": 1759516200.12,                 # epoch seconds (optional, defaults to now)
      "connection": "connected",          # optional
      "readings": {"rpm": 1726, ...},     # partial is fine; None = "ECU says unsupported"
      "trouble_codes": ["P0171"],         # optional; omit if this frame has no code update
      "mil": true                         # optional; check-engine lamp state
    }

State.apply() merges frames; State.snapshot() produces the agreed UI format.
"""
import datetime as dt
import statistics
from collections import deque

from .obd import KEY_INTERVAL

READINGS = {
    "rpm":           {"label": "Engine RPM",         "unit": "rpm"},
    "coolant_c":     {"label": "Engine temp",         "unit": "°C"},
    "ecu_voltage_v": {"label": "Battery",          "unit": "V"},
    "speed_kph":     {"label": "Speed",        "unit": "km/h"},
    "throttle_pct":  {"label": "Throttle",    "unit": "%"},
    "stft_pct":      {"label": "Fuel adjust (now)", "unit": "%"},
    "ltft_pct":      {"label": "Fuel adjust (learned)",  "unit": "%"},
    "maf_gs":        {"label": "Air flow",        "unit": "g/s"},
    "intake_c":      {"label": "Intake air temp",      "unit": "°C"},
    "load_pct":      {"label": "Engine load",          "unit": "%"},
    "map_kpa":       {"label": "Intake pressure", "unit": "kPa"},
    "timing_deg":    {"label": "Spark timing",       "unit": "°"},
    "fuel_pct":      {"label": "Fuel tank",           "unit": "%"},
    "oil_c":         {"label": "Oil temp",             "unit": "°C"},
    "ambient_c":     {"label": "Outside temp",     "unit": "°C"},
    "baro_kpa":      {"label": "Air pressure",  "unit": "kPa"},
    "runtime_min":   {"label": "Time running",      "unit": "min"},
    "mil_dist_km":   {"label": "Driven with warning light", "unit": "km"},
    "clear_dist_km": {"label": "Driven since cleared", "unit": "km"},
    "pedal_d_pct":   {"label": "Gas pedal",            "unit": "%"},
    "pedal_e_pct":   {"label": "Gas pedal sensor 2",   "unit": "%"},
    "cmd_throttle_pct": {"label": "Throttle target", "unit": "%"},
    "throttle_b_pct": {"label": "Throttle sensor 2",   "unit": "%"},
    "cat_c":         {"label": "Catalytic converter",        "unit": "°C"},
    "lambda":        {"label": "Air-fuel mix", "unit": "λ"},
    "cmd_lambda":    {"label": "Air-fuel target",     "unit": "λ"},
    "o2_up_v":       {"label": "Oxygen sensor (front)",    "unit": "V"},
    "o2_down_v":     {"label": "Oxygen sensor (rear)",     "unit": "V"},
    "rail_kpa":      {"label": "Fuel pressure",   "unit": "kPa"},
    "fuel_rate_lph": {"label": "Fuel use",             "unit": "L/h"},
    "warmups":       {"label": "Warm-ups since cleared", "unit": ""},
    "mil_time_min":  {"label": "Run time with warning light", "unit": "min"},
    "clear_time_min": {"label": "Run time since cleared", "unit": "min"},
}

# What users see for each internal mode name ("replay" stays in the API so hardware scripts keep working)
MODE_LABELS = {"live": "LIVE", "simulator": "SIMULATOR", "replay": "HISTORY"}

STALE_AFTER_S = 3.0        # at least this long without an update before a reading counts as stale


def stale_after(key):
    """Slow readings (fuel level, distances) refresh every few seconds by design;
    only call them stale when they're clearly late, so tiles don't flicker."""
    return max(STALE_AFTER_S, 2.5 * KEY_INTERVAL.get(key, 0) + 1)
HISTORY_S = 180


def iso(t):
    if t is None:
        return None
    return (dt.datetime.fromtimestamp(t, dt.timezone.utc)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"))


class State:
    def __init__(self):
        self.vehicle = ""          # free text "Make Model Year Trim"; filled from the VIN in Live mode
        self.vin = None                              # set when the car reports it; vehicle then follows
        self.reset("simulator", "healthy")

    def reset(self, mode, scenario=None, recording=None):
        self.mode = mode
        self.scenario = scenario
        self.recording = recording
        self.connection = "disconnected"
        self.connection_detail = ""
        self.capturing = False
        self.readings = {k: None for k in READINGS}
        self.updated = {k: None for k in READINGS}
        self.history = {k: deque() for k in READINGS}
        self.codes = []
        self.code_status = {}      # code -> ["warning light", "stored", "pending", ...]
        self.code_checks = []      # kinds of code check the car answered: stored, pending, ...
        self.supported = None      # reading keys the car supports; None = unknown
        self.diagnostics = {}      # readiness, fuel_status, freeze_frame, misfires
        self.mil = False
        self.markers = []
        self.last_updated = None
        self.session_start = None
        self.frames = 0
        self.session = {}          # whole-capture stats per reading: n, sum, min, max (for saved reports)
        self.recording_file = None

    def clear_codes(self):
        self.codes = []
        self.code_status = {}
        self.code_checks = []
        self.diagnostics = {}
        self.mil = False

    def apply(self, frame, now):
        t = frame.get("t") or now
        if frame.get("connection"):
            self.connection = frame["connection"]
        for k, v in (frame.get("readings") or {}).items():
            if k not in READINGS:
                continue
            self.readings[k] = None if v is None else round(float(v), 2)
            self.updated[k] = t
            if v is not None:
                agg = self.session.setdefault(k, {"n": 0, "sum": 0.0, "min": float(v), "max": float(v)})
                agg["n"] += 1
                agg["sum"] += float(v)
                agg["min"], agg["max"] = min(agg["min"], float(v)), max(agg["max"], float(v))
                h = self.history[k]
                h.append((t, float(v)))
                while h and h[0][0] < t - HISTORY_S:
                    h.popleft()
        if frame.get("trouble_codes") is not None:
            self.codes = sorted({str(c).upper().strip() for c in frame["trouble_codes"] if c})
            self.code_status = {c: list(t) for c, t in (frame.get("code_status") or {}).items() if c in self.codes}
        if frame.get("diagnostics"):
            self.diagnostics.update(frame["diagnostics"])
        if frame.get("code_checks") is not None:
            self.code_checks = list(frame["code_checks"])
        if frame.get("supported") is not None:
            self.supported = [k for k in frame["supported"] if k in READINGS]
        if frame.get("vin"):
            self.vin = str(frame["vin"])[:17]
        if frame.get("vehicle"):
            self.vehicle = str(frame["vehicle"]).strip()[:80]
        if frame.get("mil") is not None:
            self.mil = bool(frame["mil"])
        self.last_updated = t
        self.frames += 1

    def add_marker(self, now, note, origin="dashboard"):
        m = {
            "t": iso(now), "epoch": now, "note": (note or "").strip()[:200] or "Symptom noticed",
            "origin": origin,
            "readings": {k: v for k, v in self.readings.items() if v is not None},
        }
        self.markers.append(m)
        return m

    def stale_keys(self, now):
        return [k for k, t in self.updated.items()
                if t is not None and now - t > stale_after(k)]

    def session_stats(self):
        """Min / max / average of every reading over the whole capture."""
        return {k: {"min": round(a["min"], 2), "max": round(a["max"], 2), "mean": round(a["sum"] / a["n"], 2), "n": a["n"]}
                for k, a in self.session.items() if a["n"]}

    def stats(self, now, window=60):
        out = {}
        for k, h in self.history.items():
            vals = [v for (t, v) in h if t >= now - window]
            if not vals:
                continue
            diffs = [b - a for a, b in zip(vals, vals[1:])]
            out[k] = {
                "min": round(min(vals), 2),
                "max": round(max(vals), 2),
                "mean": round(statistics.fmean(vals), 2),
                "latest": round(vals[-1], 2),
                # sample-to-sample jitter; high for rpm means rough running
                "roughness": round(statistics.pstdev(diffs), 2) if len(diffs) > 2 else 0.0,
                "n": len(vals),
            }
        return out

    def history_since(self, now, seconds=60):
        return {k: [[round(t, 3), v] for (t, v) in h if t >= now - seconds]
                for k, h in self.history.items()}

    def snapshot(self, now):
        return {
            "source": self.mode,
            "scenario": self.scenario,
            "recording": self.recording,
            "vehicle": self.vehicle,
            "vin": self.vin,
            "supported": self.supported,
            "code_checks": list(self.code_checks),
            "diagnostics": dict(self.diagnostics),
            "connection": self.connection,
            "connection_detail": self.connection_detail,
            "capturing": self.capturing,
            "readings": dict(self.readings),
            "reading_updated": {k: iso(t) for k, t in self.updated.items()},
            "stale": self.stale_keys(now),
            "trouble_codes": list(self.codes),
            "mil": self.mil,
            "markers": [{k: m[k] for k in ("t", "epoch", "note", "origin")} for m in self.markers[-20:]],
            "last_updated": iso(self.last_updated),
            "server_time": iso(now),
            "session": {
                "started": iso(self.session_start),
                "frames": self.frames,
                "recording_file": self.recording_file,
            },
        }
