"""Vehicle health: a 0-100 score and a status per system, computed from the
data, never by the AI (the AI only explains it).

Built to avoid false alarms:
  * Readings are judged on 60-second statistics (mean/min/max), not one sample.
  * Thresholds are conservative: only clearly abnormal values count.
  * A system with no data is "unknown", never "ok" and never "problem".
  * Intermittent-only codes (not failing now) cost very little.
"""
from .dtc_db import SEVERITY_ORDER

SYSTEMS = {
    "engine":     "Engine & ignition",
    "fuel_air":   "Fuel & air",
    "cooling":    "Cooling",
    "electrical": "Battery & charging",
    "throttle":   "Throttle & pedal",
    "emissions":  "Emissions & exhaust",
    "transmission": "Transmission",
    "network":    "Body, chassis & modules",
}
STATUS_ORDER = {"unknown": -1, "ok": 0, "watch": 1, "problem": 2}

# Which system a reading tells us about
READING_SYSTEM = {
    "rpm": "engine", "timing_deg": "engine", "load_pct": "engine", "oil_c": "engine",
    "stft_pct": "fuel_air", "ltft_pct": "fuel_air", "maf_gs": "fuel_air", "map_kpa": "fuel_air",
    "intake_c": "fuel_air", "lambda": "fuel_air", "rail_kpa": "fuel_air",
    "coolant_c": "cooling",
    "ecu_voltage_v": "electrical",
    "throttle_pct": "throttle", "pedal_d_pct": "throttle", "cmd_throttle_pct": "throttle",
    "cat_c": "emissions", "o2_down_v": "emissions", "o2_up_v": "emissions",
}

# Points off the 100 score
CODE_PENALTY = {"stop": 45, "caution": 22, "info": 4}
LIVE_FAULT_PENALTY = 12        # extra when the ECU says it's failing now or has a warning light on
WATCH_PENALTY, PROBLEM_PENALTY = 6, 20


def code_system(code):
    """Map a trouble code to a system using its standardized structure."""
    c = code.upper()
    if c[:1] == "U":
        return "network"
    if c[:1] in "BC":
        return "network"
    if c.startswith("P2") and c[2:3] == "1":         # P2100-P2199: throttle actuator, pedal, TPS
        return "throttle"
    if c in ("P0638", "P0121", "P0122", "P0123", "P0220", "P0221", "P0222", "P0223"):
        return "throttle"
    if c.startswith("P0") or c.startswith("P2"):
        return {"1": "fuel_air", "2": "fuel_air", "3": "engine", "4": "emissions",
                "5": "engine", "6": "engine", "7": "transmission", "8": "transmission"}.get(c[2:3], "engine")
    return "engine"                                  # manufacturer P1/P3 codes: engine computer


def _status(systems, key, status, reason):
    s = systems[key]
    if STATUS_ORDER[status] > STATUS_ORDER[s["status"]]:
        s["status"] = status
    if reason:
        s["reasons"].append(reason)


def assess(code_details, readings, stats, codes_checked=True, diagnostics=None):
    """-> {"score": int|None, "label": str, "systems": [...], "positives": [...], "concerns": [...]}

    codes_checked: the car actually answered a trouble-code request. Without it,
    "no codes" is unknown, not good news.
    """
    systems = {k: {"key": k, "name": n, "status": "unknown", "reasons": []} for k, n in SYSTEMS.items()}
    st = lambda k: stats.get(k) or {}
    have_data = any(v is not None for v in readings.values())

    # Any live reading of a system makes it at least "ok" (we can see it working)
    for key, sys_key in READING_SYSTEM.items():
        if readings.get(key) is not None:
            _status(systems, sys_key, "ok", None)
    if codes_checked and have_data:
        # The car answered a code check; modules without codes count as OK
        for k in ("transmission", "network"):
            if systems[k]["status"] == "unknown":
                systems[k]["status"] = "ok"

    running = (st("rpm").get("mean") or 0) > 500
    concerns, positives = [], []

    # ---- readings (60 s statistics; conservative thresholds) -------------
    cool = st("coolant_c").get("max")
    if cool is not None:
        if cool >= 112:
            _status(systems, "cooling", "problem", f"Coolant reached {cool:.0f} °C (overheating range)")
        elif cool >= 105:
            _status(systems, "cooling", "watch", f"Coolant peaked at {cool:.0f} °C, warm for normal driving")
        elif cool >= 80:
            positives.append(("cooling", f"Engine is at normal operating temperature ({cool:.0f} °C)"))

    volt = st("ecu_voltage_v")
    if volt:
        if running:
            if volt["mean"] < 12.0:
                _status(systems, "electrical", "problem", f"Only {volt['mean']:.1f} V while running: the battery isn't being charged")
            elif volt["mean"] < 12.8:
                _status(systems, "electrical", "watch", f"{volt['mean']:.1f} V while running is low for a charging system (13.5-14.7 V is typical)")
            elif volt["mean"] > 15.2:
                _status(systems, "electrical", "watch", f"{volt['mean']:.1f} V is high; the charging system may be overcharging")
            elif 13.2 <= volt["mean"] <= 14.9:
                positives.append(("electrical", f"Charging system is healthy ({volt['mean']:.1f} V while running)"))
        elif volt["mean"] < 11.9:
            _status(systems, "electrical", "watch", f"Battery at {volt['mean']:.1f} V with the engine off is low")

    ltft = st("ltft_pct").get("mean")
    if ltft is not None:
        if abs(ltft) > 20:
            _status(systems, "fuel_air", "problem", f"Long-term fuel trim {ltft:+.1f}%: the engine is compensating heavily")
        elif abs(ltft) > 10:
            _status(systems, "fuel_air", "watch", f"Long-term fuel trim {ltft:+.1f}% is outside the usual ±10%")
        else:
            positives.append(("fuel_air", f"Fuel mixture is well balanced (long-term trim {ltft:+.1f}%)"))

    rpm = st("rpm")
    speed = st("speed_kph").get("max")
    if rpm and running and (speed or 0) < 3:
        if rpm.get("roughness", 0) > 60:
            _status(systems, "engine", "watch", f"Idle speed is unsteady (±{rpm['roughness']:.0f} rpm between samples)")
        elif 550 <= rpm["mean"] <= 1100 and rpm.get("roughness", 0) < 25:
            positives.append(("engine", f"Smooth, steady idle ({rpm['mean']:.0f} rpm)"))

    # ---- deeper diagnostics (conservative: only clear faults count) -----------
    diag = diagnostics or {}
    fuel = (diag.get("fuel_status") or {}).get("code")
    if fuel == 8:
        _status(systems, "fuel_air", "watch", "Fuel control fell back to open loop because of a fault")
    elif fuel == 16:
        _status(systems, "fuel_air", "watch", "Fuel control reports a sensor fault")
    misfires = {c: (v.get("recent") or 0) for c, v in (diag.get("misfires") or {}).items()}
    if misfires:
        worst_cyl, worst = max(misfires.items(), key=lambda kv: kv[1])
        others = [v for c, v in misfires.items() if c != worst_cyl]
        if worst >= 10 and worst > 3 * max(others or [0]):
            _status(systems, "engine", "watch", f"Cylinder {worst_cyl} has far more misfires than the others ({worst})")
        elif not any(misfires.values()):
            positives.append(("engine", "No misfires recorded on any cylinder"))
    readiness = diag.get("readiness") or []
    if readiness and all(t["ready"] for t in readiness):
        positives.append(("emissions", "All emissions self-tests complete (ready for inspection)"))

    # ---- codes -------------------------------------------------------------
    score = 100
    for d in code_details or []:
        sys_key = code_system(d["code"])
        status = d.get("status") or []
        intermittent_only = status == ["intermittent"]
        level = "watch" if intermittent_only or d["severity"] == "info" else "problem"
        _status(systems, sys_key, level, f"{d['code']}: {d['title']}")
        score -= CODE_PENALTY["info"] if intermittent_only else CODE_PENALTY.get(d["severity"], 22)
        if {"active", "warning light"} & set(status):
            score -= LIVE_FAULT_PENALTY
        concerns.append({"code": d["code"], "title": d["title"], "severity": d["severity"], "system": SYSTEMS[sys_key]})

    for s in systems.values():
        if s["status"] == "problem" and not any(c.get("system") == s["name"] for c in concerns):
            score -= PROBLEM_PENALTY
        elif s["status"] == "watch" and not any(c.get("system") == s["name"] for c in concerns):
            score -= WATCH_PENALTY
        if s["status"] in ("watch", "problem"):
            for r in s["reasons"]:
                if not any(r.startswith(c.get("code") or "~") for c in concerns):
                    concerns.append({"code": None, "title": r, "severity": "caution" if s["status"] == "problem" else "info",
                                     "system": s["name"]})

    # Never praise a system that has a fault: a lean code and "mixture is fine" can't both be shown
    positives = [text for key, text in positives if systems[key]["status"] in ("ok", "unknown")]

    if not have_data:
        return {"score": None, "label": "No data yet", "systems": list(systems.values()),
                "positives": [], "concerns": concerns, "checked": 0}

    worst = max((SEVERITY_ORDER.get(d["severity"], 1) for d in code_details or []), default=-1)
    score = max(5, min(100, score))
    if worst >= SEVERITY_ORDER["stop"]:
        score = min(score, 40)                       # a stop-driving code can never look "good"
    elif any(s["status"] == "problem" for s in systems.values()):
        score = min(score, 74)                       # a real problem is never labelled "Good"
    label = ("Excellent" if score >= 90 else "Good" if score >= 75 else
             "Needs attention" if score >= 50 else "Serious: get it checked")
    if codes_checked and not code_details:
        positives.insert(0, "No trouble codes stored, pending or permanent")
    checked = sum(1 for s in systems.values() if s["status"] != "unknown")
    return {"score": score, "label": label, "systems": list(systems.values()),
            "positives": positives, "concerns": concerns, "checked": checked}
