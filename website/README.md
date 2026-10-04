# Otto — one less trip to the mechanic

**FREE-WILi + OBD-II + AI, by the UofI Car Guys.** Plug in, see how healthy your car is, understand what it's telling you, fix what you safely can, and walk into a shop with evidence when you can't.

```
Car (OBD-II port) ──CAN──► FREE-WILi ──USB──► Python server ──WebSocket──► Browser dashboard
                           buttons/LEDs ◄── /api/hw/*                       └► AI explain (Claude API, optional)
                                                                            └► Health score, DIY steps, saved reports
```

Everything runs locally. **No internet is needed for the demo** — without an API key the app uses its offline code database and says so.

---

## Run it (2 minutes)

```bash
pip install -r requirements.txt          # add --break-system-packages on some Linux setups
pip install --no-deps freewili==0.0.51   # live FREE-WILi source (its pins break fastapi)
cp .env.example .env                      # optional: ANTHROPIC_API_KEY (+ ANTHROPIC_WORKSPACE_ID if the key isn't workspace-scoped)
python -m uvicorn server.app:app --reload --port 8000
# http://localhost:8000            landing page (scroll tour of the car's systems)
# http://localhost:8000/dashboard  the app: pick a scenario, press Space
python -m pytest -q                       # decoder, safety, health and AI tests
```

`/#step-6` opens the landing tour at a given system (demos).

### What the owner gets
- **Vehicle health** (`server/health.py`): a 0–100 score and a status for 8 systems, computed from 60-second statistics and the codes, never by the AI. Conservative thresholds; no data = "no data", not "OK"; a system with a fault is never listed as going well; intermittent-only codes count as history.
- **Help me fix it** (`server/ai.py`): plain-language summary, what looks good, each issue with ranked causes and safe DIY steps (difficulty + tools), and whether/how urgently to see a mechanic. Claude (`claude-opus-5-5`, structured outputs, server-side refusal fallback) when `ANTHROPIC_API_KEY` is set; the same shape from the offline knowledge base otherwise.
- **Saved reports:** *Save report* writes a dated HTML + JSON to `reports/` (git-ignored: they include the VIN) and lists recent ones; print any as a PDF.
- **On the FREE-WILi itself:** Otto stays connected to the device while the server runs. Its screen shows the mode, whether the car is connected, the health score and the first trouble code; LEDs show connection (green/blue/amber/red), capturing (orange), health (green/amber/red) and the check-engine lamp. Buttons: **green** start capture, **red** stop, **grey** mark a symptom, **blue** run the AI diagnosis, **yellow** next simulator scenario. `OTTO_FREEWILI=0` leaves the device alone (e.g. a second server).
- **Find mechanics near me:** opens Google Maps for repair shops near the viewer (a make specialist when the advice says so).

---

## Safety first: read-only by design

We never touch anything that could change how the car behaves. Enforced in code, not by convention:

| Allowed (what every scan tool does) | Blocked (raises `UnsafeRequest`) |
|---|---|
| Broadcast (`0x7DF` / `0x18DB33F1`): `0x01` current data · `0x03` stored codes · `0x07` pending codes · `0x0A` permanent codes · `0x09` VIN<br>Directly to one ECU (`0x7E0–0x7E7` / `0x18DAxxF1`): only UDS `0x19 01/02` / KWP `0x18` *read fault memory*, and ISO-TP flow control | `0x04` clear codes, `0x08` component control, UDS `0x10` sessions, `0x11` reset, `0x14` clear, `0x27` unlock, `0x2E` write, `0x2F` I/O control, `0x31` routines, `0x34–0x37` reflash, `0x3D` write memory, `0x85` DTC setting, and anything else sent directly to an ECU |

- Single-frame requests only. Broadcast requests are standard OBD reads. Directly-addressed frames are limited to two things: the fixed ISO-TP flow control `30 00 00` (no service; it only lets an ECU finish a long reply) and *read fault memory*. Many ECUs (e.g. VW engine computers, where EPC faults live) only answer that read when asked directly.
- `server/obd.py:check_tx()` is the gate. `FreeWiliSource.send_can()` is the **only** transmit path and calls it.
- The web server has **no endpoint that transmits** on the vehicle bus. `/api/ingest*` only receives.
- Even safer option: passive listen-only (transmit nothing, decode what's already on the bus).
- Practical: test on the simulator first, then a bench / junkyard ECU or a team member's own car, engine off → idle. Never while driving.

Tell judges this explicitly. It answers "is this safe to plug into my car?" before they ask.

### What Live mode reads
- **Supported readings** first (Mode 01 bitmaps); the dashboard hides tiles the car can't report.
- **VIN** (Mode 09) fills in the vehicle automatically. Make, year and common VW models decode offline; if online, NHTSA's free vPIC decoder adds the exact model and trim. `OTTO_VIN_LOOKUP=0` keeps the VIN on the laptop.
- **Who answers, and how:** 11-bit IDs first, 29-bit if nothing answers (some makes use them). Works on any car with CAN OBD-II, which US law requires from model year 2008 (many earlier). Set the Neptune to the car's bus speed: 500 kbit/s for almost all cars.
- **Trouble codes four ways**, every 5 s: stored, pending, permanent (broadcast), and each answering ECU's own fault memory (UDS `19 02`, asked directly). Each code card says how it was reported (warning light, active, stored, pending, intermittent). Faults that are only *intermittent* (not failing now) are shown as Info, never as an alarm. The check-engine badge only lights when the car itself reports its lamp on (PID 01).
- **Readings at their own pace:** rpm/speed/throttle/load ~2×/s, temperatures, trims, pedal/throttle and lambda every ~2.5 s, slow values every 5–15 s. A tile only greys out when its reading is clearly late, or immediately for all tiles if the car stops answering.
- **Tire pressure is not available over OBD-II.** There is no standard PID for it; it lives in a body/TPMS module with make-specific requests, and many cars use indirect TPMS (worked out from ABS wheel speeds), which has no psi value anywhere. If the car's own dash never shows per-tire pressures, the car doesn't measure them.
- **Raw log:** every frame both ways goes to `recordings/raw-*.canlog` while capturing, so anything the decoder misses can be checked later.
- `python -m tools.can_probe 10` runs the same checks once, without the server, and prints a summary.

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
| `server/sources/freewili.py` | Live source: polls the car through FREE-WILi + Neptune (SpartaHack firmware), ISO-TP reassembly; HTTP push still works | P1 + P2 |
| `tools/can_probe.py` | First-frame test against the real car, no server | P1 |
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
