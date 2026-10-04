"""Otto server.  Run from the project root:

    python -m uvicorn server.app:app --reload --port 8000

then open http://localhost:8000
"""
import asyncio
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from . import ai, device_screen, health, obd, report                                    # noqa: E402
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
            self.state.session = {}
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
        make = (self.state.vehicle or "").split(" ")[0] or None     # make-specific notes only for that make
        details = details_for(self.state.codes, self.state.readings, stats, make)
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
            snap["health"] = self.health(snap["code_details"], stats)
            snap["explanation"] = {
                "version": self.explanation_version,
                "available": self.explanation is not None,
                "outdated": self.explanation is not None and self.explanation_codes != self.state.codes,
                "running": self.explaining,
            }
            return snap

    def health(self, code_details, stats):
        # In Live mode only trust "no codes" once the car answered a code check
        checked = bool(self.state.code_checks) or self.state.mode != "live"
        return health.assess(code_details, self.state.readings, stats, codes_checked=checked,
                             diagnostics=self.state.diagnostics)

    def context(self, now):
        with self.lock:
            stats = self.state.stats(now)
            details = self.code_details(stats)
            return {
                "vehicle": self.state.vehicle,
                "data_mode": self.state.mode,
                "scenario": self.state.scenario,
                "codes": list(self.state.codes),
                "mil": self.state.mil,
                "code_details": details,
                "health": self.health(details, stats),
                "code_checks": list(self.state.code_checks),
                "diagnostics": dict(self.state.diagnostics),
                "vin": self.state.vin,
                "readings_now": {k: v for k, v in self.state.readings.items() if v is not None},
                "stats_last_60s": stats,
                "session_stats": self.state.session_stats(),
                "recording_file": self.state.recording_file,
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

    # ---- FREE-WILi buttons, screen and LEDs --------------------------
    def button(self, color):
        """A FREE-WILi button (or POST /api/hw/button). Returns the action taken, or None."""
        action = BUTTONS.get(color.lower())
        if action == "start":
            self.start_capture()
        elif action == "stop":
            self.stop_capture()
        elif action == "marker":
            with self.lock:
                self.state.add_marker(time.time(), "Marked on FREE-WILi", "freewili")
        elif action == "next_scenario" and self.state.mode == "simulator":
            keys = list(SCENARIOS)
            self.set_source("simulator", keys[(keys.index(self.state.scenario) + 1) % len(keys)])
        elif action == "explain":
            threading.Thread(target=self.run_explain, daemon=True).start()
        else:
            return None
        return action

    def device_view(self):
        """What the FREE-WILi shows: a few short lines on its screen and 7 LEDs."""
        with self.lock:
            s = self.state
            stats = s.stats(time.time())
            h = self.health(self.code_details(stats), stats)
            if not s.capturing:
                status = "Press GREEN to start"
            else:
                status = {"connected": "Car connected", "waiting": "Waiting for car"}.get(s.connection, "Car not answering")
            score = h["score"]
            level = ("ok" if s.connection == "connected" else "warn" if s.connection == "waiting" else "bad") if s.capturing else "idle"
            screen = {
                "mode": s.mode, "status": status, "status_level": level,
                "score": score, "label": h["label"], "volts": s.readings.get("ecu_voltage_v"),
                "running": (s.readings.get("rpm") or 0) > 500, "coolant_c": s.readings.get("coolant_c"),
                "codes": list(s.codes), "capturing": s.capturing,
            }
            if not s.capturing:          # same image every time, so the device shows it instantly
                screen.update(score=None, label="Ready", volts=None, coolant_c=None, codes=[], running=False)
            text = device_screen.text_fallback(screen)
            off = (0, 0, 0)
            conn = {"connected": (0, 60, 0), "idle": (0, 20, 70), "waiting": (70, 35, 0)}.get(s.connection, (70, 0, 0))
            health_led = off if score is None else (0, 60, 0) if score >= 75 else (70, 40, 0) if score >= 50 else (70, 0, 0)
            leds = [conn, (80, 24, 0) if s.capturing else off, health_led, (70, 35, 0) if s.mil else off, off, off, off]
            return {"text": text, "leds": leds, "screen": screen}

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
        try:
            ctl.tick(now)
            snap = ctl.snapshot(now)
        except Exception:                       # never let one bad frame stop the live dashboard
            logging.getLogger("otto").exception("tick failed")
            await asyncio.sleep(TICK_S)
            continue
        for ws in list(clients):
            try:
                await ws.send_json(snap)
            except Exception:
                clients.discard(ws)
        await asyncio.sleep(TICK_S)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(tick_loop())
    if os.getenv("OTTO_FREEWILI", "1") != "0":       # 0 = don't touch the device (second server, tests)
        ctl.live.on_button = ctl.button
        ctl.live.device_view = ctl.device_view
        ctl.live.link()
    yield
    task.cancel()
    ctl.stop_capture()
    ctl.live.shutdown()


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
            "ai": {"available": ok, "detail": "AI ready" if ok else why}, "safety": safety()}


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


# ---- saved reports: documentation of the car's condition over time ------------
REPORTS_DIR = ROOT / "reports"


@app.post("/api/reports")
def save_report():
    """Save the current report (HTML + data) under reports/ and return its name."""
    ctx = ctl.context(time.time())
    REPORTS_DIR.mkdir(exist_ok=True)
    slug = "-".join((ctx["vehicle"] or "vehicle").split()).lower()
    slug = "".join(c for c in slug if c.isalnum() or c == "-")[:40] or "vehicle"
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}"
    (REPORTS_DIR / f"{name}.html").write_text(report.render_html(ctx), encoding="utf-8")
    (REPORTS_DIR / f"{name}.json").write_text(json.dumps(ctx, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    h = ctx["health"]
    return {"name": name, "url": f"/reports/{name}.html", "score": h["score"], "label": h["label"]}


@app.get("/api/reports")
def list_reports():
    REPORTS_DIR.mkdir(exist_ok=True)
    out = []
    for p in sorted(REPORTS_DIR.glob("*.json"), reverse=True)[:50]:
        try:
            ctx = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        h = ctx.get("health") or {}
        out.append({"name": p.stem, "url": f"/reports/{p.stem}.html", "vehicle": ctx.get("vehicle"),
                    "generated": ctx.get("generated"), "score": h.get("score"), "label": h.get("label"),
                    "codes": ctx.get("codes", []), "mode": ctx.get("data_mode")})
    return out


@app.get("/reports/{name}.html", response_class=HTMLResponse)
def saved_report(name: str):
    path = REPORTS_DIR / f"{Path(name).name}.html"     # no path traversal
    if not path.is_file():
        raise HTTPException(404, "Report not found")
    return path.read_text(encoding="utf-8")


@app.get("/api/report.json")
def report_json():
    # Indented so the downloaded file is readable when opened in a text editor
    body = json.dumps(ctl.context(time.time()), indent=2, ensure_ascii=False, default=str)
    return Response(body, media_type="application/json")


# ---- FREE-WILi integration ----------------------------------------------
@app.post("/api/hw/button")
async def hw_button(req: ButtonReq):
    action = await asyncio.to_thread(ctl.button, req.button)
    if action is None:
        return {"ok": False, "reason": f"button '{req.button}' has no action in mode {ctl.state.mode}"}
    return {"ok": True, "action": action}


@app.get("/api/hw/status")
def hw_status():
    return ctl.live.device_status()


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


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(ROOT / "web" / "dashboard.html")


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")   # "/" = landing page (index.html)
