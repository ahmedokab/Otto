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

READINGS = {
    "rpm":           {"label": "Engine speed",         "unit": "rpm"},
    "coolant_c":     {"label": "Coolant temp",         "unit": "°C"},
    "ecu_voltage_v": {"label": "ECU voltage",          "unit": "V"},
    "speed_kph":     {"label": "Vehicle speed",        "unit": "km/h"},
    "throttle_pct":  {"label": "Throttle position",    "unit": "%"},
    "stft_pct":      {"label": "Short-term fuel trim", "unit": "%"},
    "ltft_pct":      {"label": "Long-term fuel trim",  "unit": "%"},
    "maf_gs":        {"label": "Mass air flow",        "unit": "g/s"},
    "intake_c":      {"label": "Intake air temp",      "unit": "°C"},
}

# What users see for each internal mode name ("replay" stays in the API so hardware scripts keep working)
MODE_LABELS = {"live": "LIVE", "simulator": "SIMULATOR", "replay": "HISTORY"}

STALE_AFTER_S = 3.0
HISTORY_S = 180


def iso(t):
    if t is None:
        return None
    return (dt.datetime.fromtimestamp(t, dt.timezone.utc)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"))


class State:
    def __init__(self):
        self.vehicle = "Volkswagen Beetle 2019 SE"   # free text; the dashboard suggests "Make Model Year Trim"
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
        self.mil = False
        self.markers = []
        self.last_updated = None
        self.session_start = None
        self.frames = 0
        self.recording_file = None

    def clear_codes(self):
        self.codes = []
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
                h = self.history[k]
                h.append((t, float(v)))
                while h and h[0][0] < t - HISTORY_S:
                    h.popleft()
        if frame.get("trouble_codes") is not None:
            self.codes = sorted({str(c).upper().strip() for c in frame["trouble_codes"] if c})
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
                if t is not None and now - t > STALE_AFTER_S]

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
