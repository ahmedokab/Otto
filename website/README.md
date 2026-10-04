# Otto — understand your check-engine light

**FREE-WILi + OBD-II + AI.** Plug in, capture, and walk into the mechanic with evidence instead of a guess.

```
Car (OBD-II port) ──CAN──► FREE-WILi ──USB──► Python server ──WebSocket──► Browser dashboard
                           buttons/LEDs ◄── /api/hw/*                       └► AI explain (Claude API, optional)
                                                                            └► Printable mechanic report
```

Everything runs locally. **No internet is needed for the demo** — without an API key the app uses its offline code database and says so.

---

## Run it (2 minutes)

```bash
pip install -r requirements.txt          # add --break-system-packages on some Linux setups
cp .env.example .env                      # optional: paste ANTHROPIC_API_KEY for AI explanations
python -m uvicorn server.app:app --reload --port 8000
# open http://localhost:8000  →  pick a scenario  →  press Space
python -m pytest -q                       # decoder + safety tests (23)
```

Shortcuts: `Space` capture · `M` mark symptom · `E` explain · `1`–`6` scenarios.

---

## Safety: read-only by design

We never touch anything that could change how the car behaves. Enforced in code, not by convention:

| Allowed (what every scan tool does) | Blocked (raises `UnsafeRequest`) |
|---|---|
| `0x01` current data · `0x03` stored codes · `0x07` pending codes · `0x09` VIN | `0x04` clear codes, `0x08` component control, UDS `0x10` sessions, `0x11` reset, `0x14` clear, `0x27` unlock, `0x2E` write, `0x2F` I/O control, `0x31` routines, `0x34–0x37` reflash, `0x3D` write memory, `0x85` DTC setting |

- Only functional broadcast `0x7DF`, single-frame requests. No physically-addressed frames to a specific ECU.
- `server/obd.py:check_tx()` is the gate. `FreeWiliSource.send_can()` is the **only** transmit path and calls it.
- The web server has **no endpoint that transmits** on the vehicle bus. `/api/ingest*` only receives.
- Even safer option: passive listen-only (transmit nothing, decode what's already on the bus).
- Practical: test on the simulator first, then a bench / junkyard ECU or a team member's own car, engine off → idle. Never while driving.

Tell judges this explicitly. It answers "is this safe to plug into my car?" before they ask.

---

## Data contract (agree on this, then work in parallel)

**Frames** from any source → server (`POST /api/ingest`, partial is fine):
```json
{ "readings": {"rpm": 1726, "coolant_c": 89, "ecu_voltage_v": 13.8}, "trouble_codes": ["P0171"], "mil": true }
```
`null` = vehicle says "unsupported". Simply stop sending when unplugged → readings go **stale** after 3 s and the UI greys them out.

**Raw CAN** (let the server decode): `POST /api/ingest/can {"id":"0x7E8","data":"04410C1AF8000000"}`

**Snapshot** server → UI (`/ws`, 5 Hz; also `GET /api/state`): the agreed format plus `reading_updated`, `stale`, `code_details` (offline evidence), `markers`, `explanation` status.

**FREE-WILi device:** buttons → `POST /api/hw/button {"button":"green|red|grey|yellow|blue"}` (start · stop · mark symptom · next scenario · explain). Screen + 7 LEDs ← `GET /api/hw/display` (`line1`, `line2`, `leds[]` hex colors).

---

## Code map

| File | What it does | Owner |
|---|---|---|
| `server/obd.py` | PID formulas, DTC decoding, **read-only TX guard** | P2 |
| `server/sources/freewili.py` | Live source: HTTP push works now; in-process serial reader is a TODO with guiding questions | P1 + P2 |
| `server/sources/simulator.py` | 6 labeled fault scenarios | P3 |
| `server/sources/replay.py` | Records every capture to `recordings/*.jsonl`, replays with original timing | P2 |
| `server/dtc_db.py` | Offline code knowledge + evidence rules (data supports / contradicts the code) | P4 |
| `server/ai.py` | Grounded Claude explanation, same JSON schema as offline fallback | P4 |
| `server/report.py` | Printable mechanic report | P4 |
| `web/` | Dashboard (plain HTML/CSS/JS, no build step) | P3 |

---

## Path to a winning project

Target **Best FREE-WILi + AI** (smart, software-driven). Judging = Creativity · Completeness · Practicality. Completeness is where hackathon teams lose: *everything you claim must work live*.

### Phase 1 — tonight: first real frame (P1 + P2)
1. Wire the CAN interface to the OBD-II port: pin 6 = CAN-H, pin 14 = CAN-L, pins 4/5 = ground, pin 16 = +12 V (don't power anything from it until you've measured it). Most cars after 2008 use 500 kbit/s.
2. Get **one** `0x7E8` reply to `02 01 0C` on the FREE-WILi console. Write the raw line in this README. That's your milestone.
3. Pipe it in with the dumbest thing that works: a script that reads the console and `POST`s to `/api/ingest/can`. Optimize later.
4. Record a real session (Live mode + Start capture). **That recording is your insurance** — if the car isn't available at judging, play back real data from the History tab, labeled HISTORY.

### Phase 2 — make FREE-WILi visibly essential (P1)
5. Device script: green/red/grey/yellow/blue → `/api/hw/button`. Poll `/api/hw/display` → screen text + LEDs (connection, activity blink, mode, check-engine, severity).
6. The grey "mark symptom" button is the killer feature: the driver presses it the moment the car shudders; the marker lands on the charts and in the report with the readings at that instant. That's a story judges remember.
7. Stretch: watch-strap mode (it ships with a strap), speaker beep on a new code, accelerometer for "shudder" detection to auto-mark.

### Phase 3 — AI that earns the category (P4)
8. Add your API key; test every scenario. Check the AI ranks causes **using the numbers** (e.g. lean + LTFT +17% → vacuum leak higher). If it says something wrong, tighten `SYSTEM_PROMPT`, don't hide it.
9. Show the contrast on stage: offline card (generic causes) → AI card (ranked, cites your readings, asks your symptoms). Unplug Wi-Fi and show graceful fallback — that's completeness.

### Phase 4 — polish & proof (all)
10. Test the unplug-mid-capture path: readings must go stale, not freeze as if live.
11. Print the report as PDF; bring a paper copy to the table.
12. Freeze features 3 hours before judging. Rehearse the demo 3 times with a timer.

### Optional: Best documentation award
Separate, small PR to the FREE-WILi repo: e.g. "Reading OBD-II PIDs over CAN with FREE-WILi" — wiring diagram, the exact console commands that worked for you, a 30-line example. You'll have written all of it anyway in Phase 1. (Check with the booth whether it counts against the one-award-per-team rule.)

---

## 2-minute demo script

| Time | Do | Say |
|---|---|---|
| 0:00 | Hold up FREE-WILi on the strap | "Check-engine light comes on. You don't know if it's a gas cap or your engine dying, and the shop knows you don't know." |
| 0:15 | Press **green** on device | "FREE-WILi reads the car — read-only, it can't change anything." Readings stream in. |
| 0:35 | Press **grey** | "Felt something? Mark it. The data at that moment is saved." Marker appears on charts. |
| 0:50 | Press **yellow** → Lean scenario (clearly labeled SIMULATOR) | "Here's a fault we can't safely create in a real car." Code P0171 appears. |
| 1:05 | Point at evidence | "We don't just show the code — we check it against live data. Fuel trim +17%: the data supports it." |
| 1:20 | Press **blue** (AI) | "AI ranks causes using *your* numbers and tells you what to ask." |
| 1:45 | Open report | "You walk in with this. Better question, cheaper fix." |

---

## Learning checkpoints (answer before coding the hardware path)

1. A Mode 01 reply for PID `0x0C` is `04 41 0C 1A F8`. Why is RPM `(A·256 + B)/4` and not just `A·256 + B`?
2. You request 9 PIDs per cycle, each reply takes ~20 ms. What's your refresh rate? Which 3 PIDs deserve a faster lane?
3. A car with 3 stored codes replies to Mode 03 with a frame starting `10 ...`. What does that `1` nibble mean, and what must you send back before the rest arrives?
4. If the USB cable is pulled, what should the dashboard show within 3 seconds — and why is showing the last value as "Live" dangerous?
5. Why is functional addressing (`0x7DF`) safer for this project than talking to `0x7E0` directly?

---

## Not doing (on purpose)
Accounts, cloud deployment, maps, general chatbot, clearing codes. Finish capture → explanation → report first.
