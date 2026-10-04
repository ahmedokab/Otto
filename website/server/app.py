"""Otto server.  Run from the project root:

    python -m uvicorn server.app:app --reload --port 8000

then open http://localhost:8000
"""
import asyncio
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from . import ai, obd, report                                    # noqa: E402
from .dtc_db import SEVERITY_ORDER, details_for                                  # noqa: E402
from .sources.freewili import FreeWiliSource                     # noqa: E402
from .sources.replay import Recorder, ReplaySource, list_recordings  # noqa: E402
from .sources.simulator import SCENARIOS, SimulatorSource        # noqa: E402
from .state import MODE_LABELS, READINGS, State, iso             # noqa: E402

TICK_S = 0.2
BUTTONS = {"green": "start", "red": "stop", "grey": "marker", "gray": "marker", "white": "marker",
           "yellow": "next_scenario", "blue": "explain"}


class Controller:
    def __init__(self):
        self.lock = threading.RLock()
        self.state = State()
        self.source = SimulatorSource("healthy")
        self.recorder = None
        self.explanation = None
        self.explanation_codes = None
        self.explanation_version = 0
        self.explaining = False
        self.symptoms = ""
        self.live = FreeWiliSource(port=os.getenv("FREEWILI_PORT"))

    # ---- capture -------------------------------------------------------
    def start_capture(self):
        with self.lock:
            if self.state.capturing:
                return
            now = time.time()
            self.source.start()
            self.state.capturing = True
            self.state.session_start = now
            self.state.frames = 0
            self.state.clear_codes()
            if self.state.mode != "replay":
                name = f"{self.state.mode}-{self.state.scenario or 'car'}-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
                self.recorder = Recorder(name, {"vehicle": self.state.vehicle, "mode": self.state.mode,
                                                "scenario": self.state.scenario, "started": iso(now)})
                self.state.recording_file = name

    def stop_capture(self):
        with self.lock:
            self.state.capturing = False
            self.source.stop()
            if self.recorder:
                self.recorder.close()
                self.recorder = None

    def set_source(self, mode, scenario=None, recording=None):
        with self.lock:
            was = self.state.capturing
            self.stop_capture()
            vehicle = self.state.vehicle
            if mode == "simulator":
                self.source = SimulatorSource(scenario or "healthy")
                self.state.reset("simulator", self.source.scenario)
            elif mode == "replay":
                recs = list_recordings()
                name = recording or (recs[0] if recs else "")
                self.source = ReplaySource(name)
                self.state.reset("replay", self.source.meta.get("scenario"), name)
            elif mode == "live":
                self.source = self.live
                self.state.reset("live")
            else:
                raise HTTPException(400, f"unknown mode {mode}")
            self.state.vehicle = vehicle
            if mode != "live":
                self.state.vin = None      # a VIN belongs to the car that's plugged in
            self.explanation, self.explanation_codes = None, None
            self.explanation_version += 1
            if was:
                self.start_capture()

    def tick(self, now):
        with self.lock:
            conn, detail = self.source.status(now)
            if self.state.capturing:
                frame = self.source.poll(now)
                if frame:
                    self.state.apply(frame, now)
                    if self.recorder:
                        self.recorder.write(frame)
                self.state.connection, self.state.connection_detail = conn, detail
            else:
                self.state.connection = "idle" if conn == "connected" else conn
                self.state.connection_detail = detail if conn != "connected" else "Ready — press Start capture"

    # ---- views ---------------------------------------------------------
    def code_details(self, stats):
        """Offline evidence per code, plus how the car reported it (stored, pending, warning light...)."""
        details = details_for(self.state.codes, self.state.readings, stats)
        for d in details:
            d["status"] = self.state.code_status.get(d["code"], [])
            if d["status"] == ["intermittent"]:
                # Failed at some point since the last clear but not now: worth
                # knowing, not an alarm. Never present it as a live fault.
                d["severity"] = "info"
                d["driving"] = ("Not failing right now. The engine computer recorded this fault at some point "
                                "since codes were last cleared; mention it if the symptom comes back.")
        details.sort(key=lambda d: -SEVERITY_ORDER.get(d["severity"], 0))
        return details

    def snapshot(self, now):
        with self.lock:
            snap = self.state.snapshot(now)
            stats = self.state.stats(now)
            snap["code_details"] = self.code_details(stats)
            snap["explanation"] = {
                "version": self.explanation_version,
                "available": self.explanation is not None,
                "outdated": self.explanation is not None and self.explanation_codes != self.state.codes,
                "running": self.explaining,
            }
            return snap

    def context(self, now):
        with self.lock:
            stats = self.state.stats(now)
            return {
                "vehicle": self.state.vehicle,
                "data_mode": self.state.mode,
                "scenario": self.state.scenario,
                "codes": list(self.state.codes),
                "mil": self.state.mil,
                "code_details": self.code_details(stats),
                "code_checks": list(self.state.code_checks),
                "vin": self.state.vin,
                "readings_now": {k: v for k, v in self.state.readings.items() if v is not None},
                "stats_last_60s": stats,
                "markers": [dict(m) for m in self.state.markers],
                "symptoms": self.symptoms,
                "session_start": iso(self.state.session_start),
                "generated": iso(now),
                "explanation": self.explanation,
            }

    def run_explain(self):
        with self.lock:
            if self.explaining:
                return self.explanation
            self.explaining = True
            ctx = self.context(time.time())
        try:
            result = ai.explain(ctx)       # slow network call — outside the lock
        finally:
            with self.lock:
                self.explaining = False
        with self.lock:
            self.explanation = result
            self.explanation_codes = list(ctx["codes"])
            self.explanation_version += 1
        return result

    def hw_display(self, now):
        """What the FREE-WILi screen + 7 LEDs should show. The device polls this."""
        with self.lock:
            s = self.state
            mode_color = {"live": "#00ff40", "simulator": "#ffa000", "replay": "#6040ff"}[s.mode]
            conn_color = {"connected": "#00ff40", "idle": "#00ff40", "waiting": "#ffa000"}.get(s.connection, "#ff0000")
            activity = "#ffffff" if (s.capturing and s.frames % 2) else "#000000"
            mil_color = "#ffa000" if s.mil else "#000000"
            sev = details_for(s.codes, s.readings, {})
            worst = sev[0]["severity"] if sev else None
            alert = {"stop": "#ff0000", "caution": "#ffa000", "info": "#0060ff"}.get(worst, "#000000")
            return {
                "line1": ("REC " if s.capturing else "READY ") + MODE_LABELS[s.mode],
                "line2": (", ".join(s.codes) if s.codes else "No codes") + (f"  {s.readings['rpm']:.0f}rpm" if s.readings["rpm"] else ""),
                "leds": [conn_color, activity, mode_color, mil_color, alert, "#000000", "#000000"],
                "buttons": {"green": "start", "red": "stop", "grey": "mark symptom", "yellow": "next scenario", "blue": "explain"},
            }


ctl = Controller()
clients: set[WebSocket] = set()


async def tick_loop():
    while True:
        now = time.time()
        ctl.tick(now)
        snap = ctl.snapshot(now)
        for ws in list(clients):
            try:
                await ws.send_json(snap)
            except Exception:
                clients.discard(ws)
        await asyncio.sleep(TICK_S)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(tick_loop())
    yield
    task.cancel()
    ctl.stop_capture()


app = FastAPI(title="Otto", lifespan=lifespan)


# ---- request bodies ------------------------------------------------------
class CaptureReq(BaseModel):
    action: str = "toggle"

class SourceReq(BaseModel):
    mode: str
    scenario: str | None = None
    recording: str | None = None

class VehicleReq(BaseModel):
    name: str

class MarkerReq(BaseModel):
    note: str = ""

class ExplainReq(BaseModel):
    symptoms: str | None = None

class ButtonReq(BaseModel):
    button: str

class CanReq(BaseModel):
    id: str | int
    data: str           # hex, e.g. "04410C1AF8000000"


# ---- API -----------------------------------------------------------------
@app.get("/api/state")
def get_state():
    return ctl.snapshot(time.time())


@app.get("/api/history")
def get_history(seconds: int = 60):
    with ctl.lock:
        return ctl.state.history_since(time.time(), min(seconds, 180))


@app.get("/api/meta")
def get_meta():
    ok, why = ai.ai_available()
    return {"readings": READINGS, "scenarios": SCENARIOS, "recordings": list_recordings(),
            "ai": {"available": ok, "detail": why}, "safety": safety()}


@app.get("/api/safety")
def safety():
    """The server has no endpoint that transmits on the vehicle bus; hardware TX is allowlisted."""
    return {"read_only": True,
            "allowed_services": {f"0x{k:02X}": v for k, v in obd.SAFE_SERVICES.items()},
            "blocked_services": {f"0x{k:02X}": v for k, v in obd.BLOCKED_SERVICES.items()}}


@app.post("/api/capture")
def capture(req: CaptureReq):
    action = req.action
    if action == "toggle":
        action = "stop" if ctl.state.capturing else "start"
    ctl.start_capture() if action == "start" else ctl.stop_capture()
    return {"capturing": ctl.state.capturing}


@app.post("/api/source")
def set_source(req: SourceReq):
    ctl.set_source(req.mode, req.scenario, req.recording)
    return {"ok": True}


@app.post("/api/vehicle")
def set_vehicle(req: VehicleReq):
    with ctl.lock:
        ctl.state.vehicle = req.name.strip()[:80] or ctl.state.vehicle
    return {"vehicle": ctl.state.vehicle}


@app.post("/api/marker")
def add_marker(req: MarkerReq, origin: str = "dashboard"):
    with ctl.lock:
        return ctl.state.add_marker(time.time(), req.note, origin)


@app.post("/api/explain")
async def explain(req: ExplainReq):
    if req.symptoms is not None:
        ctl.symptoms = req.symptoms.strip()[:1000]
    return await asyncio.to_thread(ctl.run_explain)


@app.get("/api/explanation")
def get_explanation():
    return ctl.explanation or {}


@app.get("/report", response_class=HTMLResponse)
def report_html():
    return report.render_html(ctl.context(time.time()))


@app.get("/api/report.json")
def report_json():
    # Indented so the downloaded file is readable when opened in a text editor
    body = json.dumps(ctl.context(time.time()), indent=2, ensure_ascii=False, default=str)
    return Response(body, media_type="application/json")


# ---- FREE-WILi integration ----------------------------------------------
@app.post("/api/hw/button")
async def hw_button(req: ButtonReq):
    action = BUTTONS.get(req.button.lower())
    if action == "start":
        ctl.start_capture()
    elif action == "stop":
        ctl.stop_capture()
    elif action == "marker":
        with ctl.lock:
            ctl.state.add_marker(time.time(), "Marked on FREE-WILi", "freewili")
    elif action == "next_scenario" and ctl.state.mode == "simulator":
        keys = list(SCENARIOS)
        nxt = keys[(keys.index(ctl.state.scenario) + 1) % len(keys)]
        ctl.set_source("simulator", nxt)
    elif action == "explain":
        asyncio.create_task(asyncio.to_thread(ctl.run_explain))
    else:
        return {"ok": False, "reason": f"button '{req.button}' has no action in mode {ctl.state.mode}"}
    return {"ok": True, "action": action}


@app.get("/api/hw/display")
def hw_display():
    return ctl.hw_display(time.time())


@app.post("/api/ingest")
def ingest(frame: dict):
    """Hardware bridge pushes frames in the shared format (only used in LIVE mode)."""
    ctl.live.ingest_frame(frame, time.time())
    return {"ok": True, "mode": ctl.state.mode}


@app.post("/api/ingest/can")
def ingest_can(req: CanReq):
    can_id = int(req.id, 16) if isinstance(req.id, str) else req.id
    data = bytes.fromhex(req.data.replace(" ", ""))
    decoded = ctl.live.ingest_can(can_id, data, time.time())
    return {"ok": True, "decoded": decoded}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
