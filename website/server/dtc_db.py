"""Offline trouble-code knowledge base + evidence rules.

This is what makes the app work with NO internet: every finding the dashboard
shows comes from here first. The AI layer (ai.py) only adds ranking and
plain-language reasoning on top of it.

Evidence verdicts: "supports" | "against" | "neutral" | "missing"
"""

SEVERITY_ORDER = {"info": 0, "caution": 1, "stop": 2}

DTC = {
    "P0171": {
        "title": "System too lean (Bank 1)",
        "system": "Fuel & air metering",
        "severity": "caution",
        "meaning": "The engine computer is seeing more air than fuel on bank 1 and has hit the limit of how much extra fuel it is allowed to add to compensate.",
        "common_causes": [
            "Vacuum leak (cracked hose, intake gasket, PCV valve or hose)",
            "Dirty or failing mass airflow (MAF) sensor",
            "Low fuel pressure: weak pump or clogged filter",
            "Restricted fuel injectors",
            "Exhaust leak ahead of the upstream oxygen sensor",
        ],
        "next_checks": [
            "With the engine idling, listen for hissing around intake hoses and the intake manifold",
            "Compare fuel trims at idle vs ~2,500 rpm: trims that improve at higher rpm point to a vacuum leak; trims that get worse point to fuel delivery or the MAF",
            "Ask for a fuel-pressure test against the factory spec",
        ],
        "driving": "Usually OK to drive short-term, but a lean engine can run hot and start misfiring. Get it checked soon.",
        "related": ["ltft_pct", "stft_pct", "maf_gs", "rpm"],
    },
    "P0300": {
        "title": "Random / multiple cylinder misfire detected",
        "system": "Ignition & misfire",
        "severity": "caution",
        "meaning": "The computer detected cylinders failing to fire properly, and the misfires are not limited to a single cylinder.",
        "common_causes": [
            "Worn spark plugs",
            "Failing ignition coil(s) or plug wires",
            "Vacuum leak or lean condition",
            "Low fuel pressure or dirty injectors",
            "Low compression (mechanical wear) — less common",
        ],
        "next_checks": [
            "Check whether the check-engine light is flashing — that means an active, severe misfire",
            "Have the per-cylinder misfire counters read (OBD Mode 06) to see which cylinders are affected",
            "Inspect spark plugs; swap a coil between cylinders and see if the misfire follows it",
        ],
        "driving": "If the check-engine light is flashing, reduce load and stop driving soon: unburned fuel can overheat and destroy the catalytic converter.",
        "related": ["rpm", "ltft_pct", "stft_pct"],
    },
    "P0217": {
        "title": "Engine coolant over-temperature condition",
        "system": "Cooling",
        "severity": "stop",
        "meaning": "The engine has gotten hotter than its safe operating limit.",
        "common_causes": [
            "Low coolant from a leak",
            "Thermostat stuck closed",
            "Radiator cooling fan not running",
            "Failing water pump",
            "Clogged radiator; less commonly a head-gasket failure",
        ],
        "next_checks": [
            "Let the engine cool completely before opening the radiator or reservoir cap",
            "Check the coolant level and look for drips or crusty residue around hoses",
            "With the engine hot at idle, confirm the radiator fan switches on",
        ],
        "driving": "Stop driving. Running an overheating engine can warp the cylinder head or blow the head gasket — a far bigger repair.",
        "related": ["coolant_c", "rpm", "speed_kph"],
    },
    "P0128": {
        "title": "Coolant temperature below thermostat regulating temperature",
        "system": "Cooling",
        "severity": "info",
        "meaning": "The engine is taking too long to warm up to normal operating temperature.",
        "common_causes": [
            "Thermostat stuck open",
            "Faulty coolant temperature sensor",
            "Low coolant level",
        ],
        "next_checks": [
            "After 15+ minutes of driving, compare the scan-tool coolant reading with the dash gauge",
            "Ask whether the thermostat is a known wear item on this model",
            "Check the coolant level",
        ],
        "driving": "Generally safe to drive. Expect worse fuel economy and weak cabin heat until fixed.",
        "related": ["coolant_c", "intake_c"],
    },
    "P0562": {
        "title": "System voltage low",
        "system": "Charging & electrical",
        "severity": "caution",
        "meaning": "The engine computer is seeing lower supply voltage than it expects while the engine runs.",
        "common_causes": [
            "Failing alternator or voltage regulator",
            "Weak or aging battery",
            "Loose or corroded battery terminals or ground straps",
            "Worn or slipping drive belt",
        ],
        "next_checks": [
            "Measure battery voltage with the engine off (a full battery reads about 12.6 V)",
            "Measure again with the engine running — a healthy charging system usually reads about 13.5–14.7 V",
            "Inspect battery terminals and the drive belt; ask for a battery load test",
        ],
        "driving": "The car may stall or fail to restart. Avoid long trips until the charging system is checked.",
        "related": ["ecu_voltage_v", "rpm"],
    },
    "P0420": {
        "title": "Catalyst system efficiency below threshold (Bank 1)",
        "system": "Emissions",
        "severity": "info",
        "meaning": "The catalytic converter on bank 1 doesn't appear to be cleaning the exhaust as well as it should.",
        "common_causes": ["Aging catalytic converter", "Faulty downstream oxygen sensor", "Exhaust leak", "Past misfires or rich running damaged the converter"],
        "next_checks": ["Fix any misfire or fuel-trim codes first", "Check for exhaust leaks near the oxygen sensors", "Compare upstream and downstream O2 sensor activity"],
        "driving": "Usually drivable, but it can fail an emissions inspection.",
        "related": ["ltft_pct", "stft_pct"],
    },
    "P0442": {
        "title": "Evaporative emission system small leak detected",
        "system": "Emissions (EVAP)",
        "severity": "info",
        "meaning": "The system that traps fuel vapor found a small leak.",
        "common_causes": ["Gas cap not sealing", "Cracked EVAP hose", "Faulty purge or vent valve"],
        "next_checks": ["Check and reseat the gas cap", "Ask for an EVAP smoke test if it returns"],
        "driving": "Safe to drive.",
        "related": [],
    },
    "P0455": {
        "title": "Evaporative emission system large leak detected",
        "system": "Emissions (EVAP)",
        "severity": "info",
        "meaning": "The system that traps fuel vapor found a large leak.",
        "common_causes": ["Missing or loose gas cap", "Disconnected or broken EVAP hose", "Faulty purge or vent valve"],
        "next_checks": ["Check the gas cap first", "Ask for an EVAP smoke test"],
        "driving": "Safe to drive; you may smell fuel.",
        "related": [],
    },
    "P0101": {
        "title": "Mass airflow (MAF) sensor range/performance",
        "system": "Fuel & air metering",
        "severity": "caution",
        "meaning": "The airflow sensor's reading doesn't agree with what the computer expects for the engine's speed and load.",
        "common_causes": ["Dirty MAF sensor", "Air leak after the MAF", "Clogged air filter", "Wiring problem"],
        "next_checks": ["Inspect the air filter and intake ducting", "Ask whether cleaning the MAF sensor is appropriate"],
        "driving": "Usually drivable; may hesitate or run poorly.",
        "related": ["maf_gs", "rpm", "throttle_pct"],
    },
    "P0335": {
        "title": "Crankshaft position sensor 'A' circuit",
        "system": "Ignition timing",
        "severity": "caution",
        "meaning": "The computer has a problem with the signal that tells it the engine's position and speed.",
        "common_causes": ["Failing crankshaft position sensor", "Damaged wiring or connector", "Damaged reluctor ring"],
        "next_checks": ["Note any stalling or no-start events", "Inspect the sensor connector and wiring"],
        "driving": "The engine may stall or refuse to start without warning.",
        "related": ["rpm"],
    },
    "P0500": {
        "title": "Vehicle speed sensor 'A'",
        "system": "Vehicle speed",
        "severity": "caution",
        "meaning": "The computer isn't getting a believable vehicle-speed signal.",
        "common_causes": ["Failing speed sensor", "Wiring or connector problem", "ABS module issue on some vehicles"],
        "next_checks": ["Check whether the speedometer works", "Note any shifting problems (automatic transmissions)"],
        "driving": "Usually drivable; speedometer, cruise control or shifting may misbehave.",
        "related": ["speed_kph"],
    },
}

_SYSTEMS = {"P": "Powertrain", "C": "Chassis", "B": "Body", "U": "Network / communication"}
_P0_SUB = {
    "1": "Fuel & air metering", "2": "Fuel & air metering (injector circuit)",
    "3": "Ignition system or misfire", "4": "Auxiliary emissions controls",
    "5": "Vehicle speed, idle control & auxiliary inputs", "6": "Computer & output circuits",
    "7": "Transmission", "8": "Transmission",
}


def describe_unknown(code):
    """Decode the structure of any code so even unknown codes get a useful card."""
    code = code.upper()
    system = _SYSTEMS.get(code[:1], "Unknown system")
    if len(code) >= 2 and code[1] == "1":
        origin = "manufacturer-specific"
    else:
        origin = "standardized (SAE)"
    sub = _P0_SUB.get(code[2:3], "") if code.startswith("P") else ""
    area = f"{system}{' — ' + sub if sub else ''}"
    return {
        "title": f"{area} code",
        "system": area,
        "severity": "caution",
        "meaning": f"This is a {origin} {system.lower()} code. The offline database doesn't have details for it — use 'Explain with AI' or look it up for your exact vehicle.",
        "common_causes": [],
        "next_checks": ["Look up this code for your exact make, model and year"],
        "driving": "Unknown — treat with caution until it's identified.",
        "related": [],
        "known": False,
    }


def _ev(key, stat, value, verdict, note):
    return {"key": key, "stat": stat, "value": value, "verdict": verdict, "note": note}


def _missing(key):
    return _ev(key, None, None, "missing", "Not reported by this vehicle / data source yet")


def evidence_for(code, readings, stats):
    """Check live data against what this code predicts. Returns a list of evidence items."""
    s = lambda k: stats.get(k) or {}
    out = []
    if code == "P0171":
        lt = s("ltft_pct").get("mean")
        st = s("stft_pct").get("mean")
        if lt is None:
            out.append(_missing("ltft_pct"))
        elif lt > 10:
            out.append(_ev("ltft_pct", "mean", lt, "supports", "Above +10%: the ECU has been adding extra fuel long-term"))
        elif lt < 5:
            out.append(_ev("ltft_pct", "mean", lt, "against", "Near normal — the ECU isn't compensating much right now"))
        else:
            out.append(_ev("ltft_pct", "mean", lt, "neutral", "Slightly positive — borderline"))
        if st is not None:
            out.append(_ev("stft_pct", "mean", st,
                           "supports" if st > 5 else "neutral",
                           "Short-term trim also adding fuel" if st > 5 else "Short-term trim not strongly positive"))
    elif code == "P0300":
        r = s("rpm").get("roughness")
        if r is None:
            out.append(_missing("rpm"))
        else:
            out.append(_ev("rpm", "roughness", r, "supports" if r > 40 else "against",
                           "Engine speed is unsteady sample-to-sample — consistent with misfires" if r > 40
                           else "Engine speed looks smooth during this capture"))
        lt = s("ltft_pct").get("mean")
        if lt is not None and lt > 10:
            out.append(_ev("ltft_pct", "mean", lt, "supports", "Lean fuel trims can cause misfires — check the lean condition too"))
    elif code == "P0217":
        mx = s("coolant_c").get("max")
        if mx is None:
            out.append(_missing("coolant_c"))
        else:
            out.append(_ev("coolant_c", "max", mx,
                           "supports" if mx >= 108 else ("neutral" if mx >= 100 else "against"),
                           "Peak temperature is well above the normal operating range" if mx >= 108
                           else ("Running warm, near the top of normal" if mx >= 100
                                 else "Temperature is normal during this capture (the code may be from earlier)")))
    elif code == "P0128":
        mx = s("coolant_c").get("max")
        if mx is None:
            out.append(_missing("coolant_c"))
        else:
            out.append(_ev("coolant_c", "max", mx, "supports" if mx < 75 else "against",
                           "Engine never reached normal operating temperature" if mx < 75
                           else "Engine reached normal temperature during this capture"))
    elif code == "P0562":
        v = s("ecu_voltage_v")
        rpm = s("rpm").get("mean")
        if not v:
            out.append(_missing("ecu_voltage_v"))
        else:
            running = rpm is not None and rpm > 500
            low = v["min"] < 12.5 and running
            out.append(_ev("ecu_voltage_v", "min", v["min"], "supports" if low else "against",
                           "Voltage is below what a running engine's charging system should hold" if low
                           else "Voltage looks normal for the current engine state"))
    else:
        entry = DTC.get(code)
        for k in (entry or {}).get("related", [])[:3]:
            st = s(k)
            out.append(_ev(k, "latest", st.get("latest"), "neutral" if st else "missing",
                           "Related reading" if st else "Not reported yet"))
    return out


def details_for(codes, readings, stats):
    items = []
    for c in codes:
        base = DTC.get(c)
        d = dict(base, known=True) if base else describe_unknown(c)
        d["code"] = c
        d["evidence"] = evidence_for(c, readings, stats)
        items.append(d)
    items.sort(key=lambda d: -SEVERITY_ORDER.get(d["severity"], 1))
    return items
