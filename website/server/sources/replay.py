"""Replay a recorded session (.jsonl, one frame per line) with original timing, looping."""
import json
from pathlib import Path

from .base import Source

RECORDINGS_DIR = Path(__file__).resolve().parents[2] / "recordings"


def list_recordings():
    RECORDINGS_DIR.mkdir(exist_ok=True)
    return sorted((p.name for p in RECORDINGS_DIR.glob("*.jsonl")), reverse=True)


class Recorder:
    def __init__(self, name, meta):
        RECORDINGS_DIR.mkdir(exist_ok=True)
        self.path = RECORDINGS_DIR / name
        self.f = self.path.open("w")
        self.f.write(json.dumps({"_meta": meta}) + "\n")

    def write(self, frame):
        self.f.write(json.dumps(frame) + "\n")

    def close(self):
        self.f.close()


class ReplaySource(Source):
    mode = "replay"

    def __init__(self, name):
        self.name = name
        self.frames, self.meta = [], {}
        path = RECORDINGS_DIR / Path(name).name   # no path traversal
        if path.exists():
            for line in path.read_text().splitlines():
                if not line.strip():
                    continue
                obj = json.loads(line)
                if "_meta" in obj:
                    self.meta = obj["_meta"]
                elif "t" in obj:
                    self.frames.append(obj)
        t_first = self.frames[0]["t"] if self.frames else 0
        self.offsets = [f["t"] - t_first for f in self.frames]
        self.t0, self.i = None, 0

    def start(self):
        self.t0, self.i = None, 0

    def status(self, now):
        if not self.frames:
            return "disconnected", f"Saved session '{self.name}' not found or empty"
        return "connected", f"Playing saved session ({len(self.frames)} frames, loops)"

    def poll(self, now):
        if not self.frames:
            return None
        if self.t0 is None:
            self.t0, self.i = now, 0
        elapsed = now - self.t0
        merged = None
        while self.i < len(self.frames) and self.offsets[self.i] <= elapsed:
            f = self.frames[self.i]
            if merged is None:
                merged = {"readings": {}}
            merged["readings"].update(f.get("readings") or {})
            if f.get("diagnostics"):
                merged.setdefault("diagnostics", {}).update(f["diagnostics"])
            for k in ("trouble_codes", "code_status", "code_checks", "mil", "connection", "supported", "vin", "vehicle"):
                if f.get(k) is not None:
                    merged[k] = f[k]
            self.i += 1
        if self.i >= len(self.frames):
            self.t0, self.i = None, 0
        if merged:
            merged["t"] = now
        return merged
