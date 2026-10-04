"""AI explanation layer (Claude API) with a deterministic offline fallback.

What the owner gets, in plain words: how healthy the car is, what's going
well, what's wrong, what they can safely try themselves, and whether (and
how urgently) to see a mechanic. "One less trip to the mechanic" means a good
first pass, never a risky one.

Design rules:
  * GROUNDED: the model receives the offline knowledge-base entry, evidence
    verdicts, the computed health assessment and real measurement stats.
    The health score is computed in health.py; the AI explains it, never
    invents it.
  * Structured outputs: the API guarantees the reply matches SCHEMA.
  * No API key, no internet, a refusal or any error -> the offline
    explanation in the SAME shape. The demo never breaks.
"""
import hashlib
import json
import os

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("AI_EFFORT", "medium")
TIMEOUT_S = float(os.getenv("AI_TIMEOUT_S", "120"))
FALLBACK_BETA = "server-side-fallback-2026-07-01"   # fallbacks="default": a declined request re-runs on another model

SYSTEM_PROMPT = """You are the diagnostic assistant inside Otto, built by the UofI Car Guys. Otto's promise is "one less trip to the mechanic": help a car owner understand what their car is telling them, fix what they safely can, and know when a professional is the right call.

You receive JSON with: the vehicle; trouble codes and how the car reported each one (stored, pending, permanent, active, warning light, intermittent); which code checks the car answered; an offline reference entry and evidence checks per code; a health assessment computed from the data (score, per-system status, positives, concerns); live readings and 60-second statistics; symptom markers the driver pressed; and the owner's own description.

How to write:
- Write for someone who has never opened a hood. Short sentences. Explain any technical term in a few words the first time.
- Lead with what matters most. If the car looks healthy, say so plainly and point to the readings that show it.
- Explain the health score in your own words. Do not change the score or invent a different one.
- Only cite numbers that appear in the input. Never invent readings, codes or history.
- A trouble code identifies a detected condition; it does not prove a part has failed. Say "points to", "consistent with", "worth checking".
- Rank likely causes using the measurements and symptoms, and say which numbers moved a cause up or down. If the data contradicts a code, say so.
- "Intermittent" codes are not failing right now; treat them as history worth mentioning, not as an alarm. "Pending" means seen once and not yet confirmed.
- If a code check was not answered by the car, don't claim there are no codes of that kind.
- Make it specific to this vehicle (make, model, year, VIN when given): use that make's names for its warning lights and systems, and mention well-known issues for that model when they fit the data. Never assume a different make. If the vehicle is unknown, stay general.

Fixes the owner can try (diy_steps):
- Only suggest steps that are safe for an untrained owner with basic tools: visual checks, checking fluid levels on a cool engine, tightening a gas cap, checking battery terminals, cleaning parts that are commonly cleaned at home, checking connectors are seated, re-testing after a drive.
- Order them cheapest and easiest first. Rate each step's difficulty honestly and list the tools.
- Never suggest clearing codes as a fix (the fault returns and emissions readiness resets), bypassing or disabling safety or emissions systems, working on brakes, airbags, fuel lines, high-voltage hybrid/EV parts, or anything under a raised car without proper stands.
- Say when to stop trying and hand it to a professional (mechanic_needed_if).

Safety and mechanic advice:
- If anything suggests overheating, a flashing check-engine light, loss of power while driving, a charging failure, or brake/steering issues, say clearly whether it is safe to keep driving.
- Recommend a mechanic when the fix needs special tools, lifting the car, diagnostics beyond this data, or when the risk is serious. Say how urgent it is and what kind of shop fits (for example a dealer or make specialist for manufacturer-specific codes).
- going_well: concrete things the data shows are healthy. Empty only if there is no data."""

_STR = {"type": "string"}
_STRS = {"type": "array", "items": _STR}
SCHEMA = {
    "type": "object",
    "properties": {
        "headline": _STR,
        "health_summary": _STR,
        "safety": {
            "type": "object",
            "properties": {"level": {"type": "string", "enum": ["ok", "caution", "stop"]}, "message": _STR},
            "required": ["level", "message"], "additionalProperties": False,
        },
        "going_well": _STRS,
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": _STR,
                    "title": _STR,
                    "plain_meaning": _STR,
                    "what_the_data_shows": _STRS,
                    "likely_causes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"cause": _STR,
                                           "likelihood": {"type": "string", "enum": ["higher", "medium", "lower"]},
                                           "why": _STR},
                            "required": ["cause", "likelihood", "why"], "additionalProperties": False,
                        },
                    },
                    "diy_steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"step": _STR,
                                           "difficulty": {"type": "string", "enum": ["easy", "moderate", "advanced"]},
                                           "tools": _STR},
                            "required": ["step", "difficulty", "tools"], "additionalProperties": False,
                        },
                    },
                    "mechanic_needed_if": _STR,
                },
                "required": ["code", "title", "plain_meaning", "what_the_data_shows", "likely_causes",
                             "diy_steps", "mechanic_needed_if"],
                "additionalProperties": False,
            },
        },
        "mechanic": {
            "type": "object",
            "properties": {"recommended": {"type": "boolean"},
                           "urgency": {"type": "string", "enum": ["now", "soon", "when_convenient", "not_needed"]},
                           "why": _STR, "shop_type": _STR},
            "required": ["recommended", "urgency", "why", "shop_type"], "additionalProperties": False,
        },
        "questions_for_mechanic": _STRS,
        "caveat": _STR,
    },
    "required": ["headline", "health_summary", "safety", "going_well", "findings", "mechanic",
                 "questions_for_mechanic", "caveat"],
    "additionalProperties": False,
}


def _context_key(ctx):
    raw = json.dumps({k: ctx.get(k) for k in ("vehicle", "codes", "symptoms", "markers")}, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _difficulty(step):
    s = step.lower()
    if any(w in s for w in ("ask ", "shop", "mechanic", "dealer", "adaptation", "replace", "test against")):
        return "advanced"
    if any(w in s for w in ("clean", "inspect", "swap", "connector", "wiring")):
        return "moderate"
    return "easy"


def offline_explanation(ctx):
    details = ctx["code_details"]
    health = ctx.get("health") or {}
    order = {"info": 0, "caution": 1, "stop": 2}
    worst = max((d["severity"] for d in details), key=lambda s: order[s], default=None)
    level = {"info": "ok", "caution": "caution", "stop": "stop"}.get(worst, "ok")
    score, label = health.get("score"), health.get("label", "")
    vehicle = ctx.get("vehicle") or "your car"

    if score is None:
        headline = "Start a capture so Otto can read the car."
        summary = "There's no data from the car yet."
    elif not details:
        headline = f"Health {score}/100 ({label}). No trouble codes reported."
        summary = (f"Otto checked {health.get('checked', 0)} systems on {vehicle} and found no trouble codes. "
                   "That's a good sign, though it can't rule out problems the car doesn't monitor. "
                   "If something feels off, press the marker button when it happens so the data is captured.")
    else:
        headline = (f"Health {score}/100 ({label}). {len(details)} code{'s' if len(details) != 1 else ''}: "
                    + ", ".join(f"{d['code']} ({d['title']})" for d in details[:3]) + ".")
        summary = f"{vehicle[:1].upper()}{vehicle[1:]} reported {len(details)} trouble code{'s' if len(details) != 1 else ''}. " + \
                  next(d["driving"] for d in details if d["severity"] == worst)

    findings = []
    for d in details:
        findings.append({
            "code": d["code"],
            "title": d["title"],
            "plain_meaning": d["meaning"],
            "what_the_data_shows": [e["note"] for e in d["evidence"] if e["verdict"] in ("supports", "against")],
            "likely_causes": [{"cause": c, "likelihood": "medium" if i < 2 else "lower",
                               "why": "Common cause for this code (not ranked against your data offline)"}
                              for i, c in enumerate(d["common_causes"])],
            "diy_steps": [{"step": s, "difficulty": _difficulty(s), "tools": ""} for s in d["next_checks"]],
            "mechanic_needed_if": "The light stays on or comes back after these checks, or the car drives differently.",
        })
    unknown = any(not d.get("known", True) for d in details)
    urgent = worst == "stop"
    recommended = urgent or unknown or worst == "caution"
    return {
        "headline": headline,
        "health_summary": summary,
        "safety": {"level": level,
                   "message": next((d["driving"] for d in details if d["severity"] == worst), "No warning signs in the data right now.")},
        "going_well": list(health.get("positives") or []),
        "findings": findings,
        "mechanic": {
            "recommended": recommended,
            "urgency": "now" if urgent else "soon" if worst == "caution" else "when_convenient" if recommended else "not_needed",
            "why": ("This code means the car shouldn't be driven far." if urgent else
                    "Otto's offline database doesn't cover this code in detail." if unknown else
                    "Try the easy checks first; if the light stays on, a shop can confirm the cause." if recommended else
                    "Nothing in the data needs a mechanic right now."),
            "shop_type": "Dealer or make specialist" if unknown else "General auto repair shop",
        },
        "questions_for_mechanic": [
            "Which test will you run to confirm the cause before replacing parts?",
            "Can you show me the freeze-frame data stored with this code?",
        ] if details else [],
        "caveat": "Offline reference: causes are typical for these codes, not ranked against your live data. Add an API key for a personalised analysis.",
        "source": "offline",
        "model": None,
    }


def ai_available():
    if not os.getenv("ANTHROPIC_API_KEY"):
        return False, "No ANTHROPIC_API_KEY set: using the offline knowledge base"
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False, "anthropic package not installed: using the offline knowledge base"
    return True, MODEL


def _payload(ctx):
    keys = ("vehicle", "vin", "data_mode", "codes", "code_checks", "code_details", "health",
            "readings_now", "stats_last_60s", "markers", "symptoms")
    return {k: ctx.get(k) for k in keys}


def explain(ctx):
    fallback = offline_explanation(ctx)
    fallback["key"] = _context_key(ctx)
    ok, why = ai_available()
    if not ok:
        fallback["note"] = why
        return fallback
    import anthropic
    try:
        client = anthropic.Anthropic(timeout=TIMEOUT_S, max_retries=1)
        msg = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            system=SYSTEM_PROMPT,
            output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": json.dumps(_payload(ctx), default=str)}],
        )
        if msg.stop_reason == "refusal":
            raise RuntimeError("the model declined this request")
        if msg.stop_reason == "max_tokens":
            raise RuntimeError("the answer was cut off")
        text = next(b.text for b in msg.content if b.type == "text")
        obj = json.loads(text)
        obj.update(source="ai", model=msg.model, key=fallback["key"])
        return obj
    except anthropic.AuthenticationError:
        fallback["note"] = "The API key was rejected: showing the offline explanation"
    except anthropic.RateLimitError:
        fallback["note"] = "AI is busy (rate limited): showing the offline explanation"
    except anthropic.APIConnectionError:
        fallback["note"] = "No internet connection: showing the offline explanation"
    except anthropic.APIStatusError as e:
        fallback["note"] = f"AI error {e.status_code}: showing the offline explanation"
    except Exception as e:             # refusal, cut-off, unexpected shape
        fallback["note"] = f"AI unavailable ({e}): showing the offline explanation"
    return fallback
