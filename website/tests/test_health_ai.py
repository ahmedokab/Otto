"""Vehicle health, the AI layer (no real API calls) and saved reports."""
import json
from types import SimpleNamespace

import pytest

from server import ai, health

HEALTHY_STATS = {"rpm": {"mean": 740, "roughness": 12}, "coolant_c": {"max": 90}, "ecu_voltage_v": {"mean": 14.0},
                 "ltft_pct": {"mean": 2.3}, "speed_kph": {"max": 0}}
HEALTHY_NOW = {"rpm": 740, "coolant_c": 90, "ecu_voltage_v": 14.0, "ltft_pct": 2.3, "speed_kph": 0}
EPC = {"code": "P2101", "title": "Throttle actuator", "severity": "caution", "status": ["warning light", "active", "stored"],
       "known": True, "meaning": "m", "driving": "Usually drivable.", "evidence": [], "common_causes": ["Dirty throttle body"],
       "next_checks": ["Inspect the throttle body connector", "Ask a shop for an adaptation"]}


def test_healthy_car_scores_high_and_says_why():
    h = health.assess([], HEALTHY_NOW, HEALTHY_STATS)
    assert h["score"] == 100 and h["label"] == "Excellent"
    assert any("Charging system is healthy" in p for p in h["positives"])
    assert {s["key"]: s["status"] for s in h["systems"]}["emissions"] == "unknown"   # no data is not "ok"

def test_active_warning_light_is_never_good():
    h = health.assess([EPC], HEALTHY_NOW, HEALTHY_STATS)
    assert h["score"] < 75 and h["label"] == "Needs attention"
    assert {s["key"]: s["status"] for s in h["systems"]}["throttle"] == "problem"

def test_intermittent_history_is_not_an_alarm():
    old = dict(EPC, code="P0441", severity="info", status=["intermittent"])
    h = health.assess([old], HEALTHY_NOW, HEALTHY_STATS)
    assert h["score"] >= 90
    assert {s["key"]: s["status"] for s in h["systems"]}["fuel_air"] != "problem"

def test_stop_code_caps_score():
    hot = dict(EPC, code="P0217", severity="stop", status=["stored"])
    assert health.assess([hot], HEALTHY_NOW, HEALTHY_STATS)["score"] <= 40

def test_single_spike_is_not_a_low_voltage_alarm():
    stats = dict(HEALTHY_STATS, ecu_voltage_v={"mean": 13.9, "min": 11.5})     # one dip, normal average
    h = health.assess([], HEALTHY_NOW, stats)
    assert {s["key"]: s["status"] for s in h["systems"]}["electrical"] == "ok"

def test_no_code_answer_means_no_all_clear():
    h = health.assess([], HEALTHY_NOW, HEALTHY_STATS, codes_checked=False)
    assert not any("No trouble codes" in p for p in h["positives"])

def test_no_data():
    assert health.assess([], {}, {})["score"] is None

def test_code_systems():
    assert [health.code_system(c) for c in ("P2101", "P0300", "P0171", "U0100", "P0700", "P0420")] == \
        ["throttle", "engine", "fuel_air", "network", "transmission", "emissions"]


CTX = {"vehicle": "Volkswagen Beetle 2013", "vin": None, "data_mode": "live", "codes": ["P2101"], "code_checks": ["stored"],
       "code_details": [EPC], "health": health.assess([EPC], HEALTHY_NOW, HEALTHY_STATS), "readings_now": HEALTHY_NOW,
       "stats_last_60s": HEALTHY_STATS, "markers": [], "symptoms": ""}


def _matches_schema(obj):
    """Light check that an answer has every required field the UI reads."""
    assert set(ai.SCHEMA["required"]) <= set(obj)
    for f in obj["findings"]:
        assert set(ai.SCHEMA["properties"]["findings"]["items"]["required"]) <= set(f)
    assert set(ai.SCHEMA["properties"]["mechanic"]["required"]) <= set(obj["mechanic"])

def test_offline_explanation_has_the_full_shape(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ex = ai.explain(CTX)
    _matches_schema(ex)
    assert ex["source"] == "offline" and "API_KEY" in ex["note"]
    assert ex["findings"][0]["diy_steps"][1]["difficulty"] == "advanced"     # "Ask a shop..." is not DIY
    assert ex["mechanic"]["recommended"] and ex["going_well"]


class FakeMessages:
    def __init__(self, reply, stop_reason="end_turn"):
        self.reply, self.stop_reason, self.kwargs = reply, stop_reason, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        text = json.dumps(self.reply)
        return SimpleNamespace(stop_reason=self.stop_reason, model=kwargs["model"],
                               content=[SimpleNamespace(type="text", text=text)])

def _fake_client(monkeypatch, messages):
    import anthropic
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: SimpleNamespace(beta=SimpleNamespace(messages=messages)))

def test_ai_request_is_grounded_and_structured(monkeypatch):
    reply = ai.offline_explanation(CTX)
    for k in ("source", "model"):
        reply.pop(k)
    fake = FakeMessages(reply)
    _fake_client(monkeypatch, fake)
    ex = ai.explain(CTX)
    assert ex["source"] == "ai" and ex["model"] is None          # never shown to users
    kw = fake.kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["output_config"]["format"]["schema"] is ai.SCHEMA
    sent = json.loads(kw["messages"][0]["content"])
    assert sent["health"]["score"] == CTX["health"]["score"] and sent["code_details"][0]["code"] == "P2101"

def test_refusal_falls_back_to_offline(monkeypatch):
    _fake_client(monkeypatch, FakeMessages({}, stop_reason="refusal"))
    ex = ai.explain(CTX)
    assert ex["source"] == "offline" and "declined" in ex["note"]


def test_saved_reports_round_trip(tmp_path, monkeypatch):
    from server import app as app_module
    monkeypatch.setattr(app_module, "REPORTS_DIR", tmp_path)
    saved = app_module.save_report()
    assert (tmp_path / f"{saved['name']}.html").read_text(encoding="utf-8").count("UofI Car Guys") >= 1
    [listed] = app_module.list_reports()
    assert listed["name"] == saved["name"] and listed["url"].endswith(".html")
    assert "Otto vehicle report" in app_module.saved_report(saved["name"])
    with pytest.raises(Exception):
        app_module.saved_report("../../server/app")                      # no path traversal

def test_a_faulty_system_is_never_praised():
    lean = dict(EPC, code="P0171", title="System too lean", severity="caution", status=["stored"])
    h = health.assess([lean], HEALTHY_NOW, HEALTHY_STATS)        # trims look fine right now
    assert not any("Fuel mixture" in p for p in h["positives"])
    assert any("Charging" in p for p in h["positives"])           # other systems still get credit

def test_saved_report_documents_the_whole_capture(tmp_path, monkeypatch):
    import time as _t
    from server import app as app_module
    from server.app import Controller
    monkeypatch.setattr(app_module, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(app_module, "Recorder", lambda *a, **k: SimpleNamespace(write=lambda f: None, close=lambda: None))
    monkeypatch.setattr(app_module, "ctl", Controller())
    c = app_module.ctl
    c.set_source("simulator", "lean")
    c.start_capture()
    t = _t.time()
    for i in range(200):                                  # 40 s of driving
        c.tick(t + i * 0.2)
    c.state.add_marker(t + 30, "Hesitation pulling away")
    saved = app_module.save_report()
    data = json.loads((tmp_path / f"{saved['name']}.json").read_text(encoding="utf-8"))
    html = (tmp_path / f"{saved['name']}.html").read_text(encoding="utf-8")
    assert data["codes"] == ["P0171"] and data["health"]["score"] is not None
    assert data["session_stats"]["rpm"]["n"] > 30            # every rpm sample, not just the last minute
    assert data["markers"][0]["note"] == "Hesitation pulling away"
    for text in ("whole capture", "P0171", "Engine RPM", "Hesitation pulling away", "Vehicle health"):
        assert text in html
    c.stop_capture()


def test_reading_concern_plus_code_does_not_crash():
    """Regression: a reading-based concern (no code) next to a coded one crashed health.assess."""
    stats = dict(HEALTHY_STATS, ecu_voltage_v={"mean": 12.3})                  # low charging -> watch, no code
    misfire = dict(EPC, code="P0300", title="Misfire", severity="caution", status=["stored"])
    diag = {"misfires": {"1": {"recent": 0}, "2": {"recent": 0}, "3": {"recent": 40}, "4": {"recent": 0}}}
    h = health.assess([misfire], HEALTHY_NOW, stats, diagnostics=diag)
    assert h["score"] < 75
    assert any("Cylinder 3" in r for s in h["systems"] for r in s["reasons"])

def test_switching_cars_clears_the_old_car():
    from server.state import State
    st = State()
    st.reset("live")
    st.apply({"vin": "3VWFP7AT5DM688634", "vehicle": "Volkswagen Beetle 2013", "readings": {"rpm": 700},
              "trouble_codes": ["P0300"], "diagnostics": {"fuel_status": {"code": 2, "text": "Normal"}}}, 1.0)
    st.add_marker(1.5, "old car shudder")
    st.apply({"vin": "KMHLW4AK1SU000001", "vehicle": "Hyundai Elantra 2025", "readings": {"coolant_c": 85}}, 2.0)
    assert st.vin == "KMHLW4AK1SU000001" and st.vehicle == "Hyundai Elantra 2025"
    assert st.readings["rpm"] is None and st.readings["coolant_c"] == 85
    assert st.codes == [] and st.diagnostics == {} and st.markers == []
    assert st.vehicle_changed

def test_new_capture_and_new_vehicle_button_start_clean(monkeypatch):
    from server import app as app_module
    monkeypatch.setattr(app_module, "Recorder", lambda *a, **k: SimpleNamespace(write=lambda f: None, close=lambda: None))
    c = app_module.Controller()
    c.set_source("live")
    c.state.vehicle, c.state.vin = "Volkswagen Beetle 2013", "3VWFP7AT5DM688634"
    c.state.apply({"readings": {"rpm": 700}, "trouble_codes": ["P0300"]}, 1.0)
    c.explanation = {"headline": "old"}
    c.live.start = c.live.stop = lambda: None           # no hardware in tests
    c.start_capture()
    assert c.state.readings["rpm"] is None and c.state.codes == [] and c.explanation is None
    assert c.state.vehicle == "" and c.state.vin is None  # re-read from whichever car is plugged in
    c.state.vehicle = "Hyundai Elantra 2025"
    c.new_vehicle()
    assert c.state.vehicle == "" and c.state.readings["rpm"] is None
