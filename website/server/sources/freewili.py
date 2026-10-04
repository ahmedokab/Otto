"""LIVE source: real vehicle data via FREE-WILi + Neptune Orca.

Car OBD-II --CAN--> Neptune --> FREE-WILi (SpartaHack firmware, Main V92 /
Display V67) --USB--> this file, through the official `freewili` package
(pip install freewili). Its CAN API: can_enable_streaming() makes the
firmware report every received frame as a CANRX0/CANRX1 event;
can_transmit() sends one.

Two ways to feed the dashboard; both end up in the same queue, so the UI
doesn't care:

  A) In-process (this file): start() opens the FREE-WILi and polls the car.
  B) HTTP push from any other process:
       POST /api/ingest       {"readings": {"rpm": 1726}, "trouble_codes": [...]}
       POST /api/ingest/can   {"id": "0x7E8", "data": "04410C1AF8000000"}

What it asks the car, in order:
  1. Which Mode 01 PIDs it supports (bitmaps), so the dashboard only shows real readings
  2. The VIN (Mode 09), to fill in the vehicle automatically
  3. Every few seconds, trouble codes four ways: stored (03), pending (07),
     permanent (0A) and the ECU's own fault memory (UDS 19 02), which is where
     manufacturer faults like VW's EPC light live
  4. Live readings, fast ones every round

Every frame in both directions is also written to recordings/raw-*.canlog
while capturing, so anything the decoder misses can be checked afterwards.

SAFETY: this project is READ-ONLY. Every transmit goes through send_can(),
which calls obd.check_tx(). Never add another path that writes to the bus.

Env:
  FREEWILI_CAN_CHANNEL  Neptune channel wired to the car (default 0 = channel A,
                        DB15 pins 6/14, GND 8)
  FREEWILI_PORT         serial number to pick when several FREE-WILis are plugged in
  FREEWILI_BENCH_ECU    simulator scenario (e.g. lean) to answer as a fake car on the
                        other Neptune channel; bench only, see sources/bench_ecu.py
  OTTO_VIN_LOOKUP       0 = decode the VIN offline only (never send it to NHTSA)
"""
import collections
import os
import queue
import threading
import time

from .base import Source
from .bench_ecu import BenchEcu
from .replay import RECORDINGS_DIR
from .. import obd, vin as vin_decoder

NO_DATA_AFTER_S = 2.0
CAN_CHANNEL = int(os.getenv("FREEWILI_CAN_CHANNEL", "0"))
REPLY_TIMEOUT_S = 0.1      # move on if no ECU answers a request
DTC_EVERY_S = 5.0          # trouble codes change rarely
VIN_RETRY_S = 10.0
VIN_TRIES = 3
RETRY_AFTER_S = 2.0        # reopen the device after unplug / error
BENCH_SCENARIO = os.getenv("FREEWILI_BENCH_ECU")
BENCH_CHANNEL = 1 - CAN_CHANNEL

# Broadcast to every ECU: stored, pending, permanent codes
DTC_REQUESTS = [obd.build_request(0x03), obd.build_request(0x07), obd.build_request(0x0A)]
# Sent to each ECU that answered, one at a time: its own fault memory. Many
# ECUs (e.g. VW engine computers, where EPC faults live) ignore it as a broadcast.
UDS_FAULT_MEMORY = obd.build_request(*obd.UDS_READ_DTCS)
# Order tags are shown in, most important first
TAG_ORDER = ["warning light", "active", "stored", "pending", "permanent", "intermittent"]
CHECK_NAMES = {"stored": "stored", "pending": "pending", "permanent": "permanent", "uds": "manufacturer"}
FRAME_KEYS = ("trouble_codes", "code_status", "code_checks", "mil", "connection", "supported", "vin", "vehicle")
DEEP_EVERY_S = 30.0        # freeze frame + misfire counters change rarely
DEEP_FIRST_S = 3.0         # first deep check after the first round of codes and readings


class FreeWiliSource(Source):
    mode = "live"

    def __init__(self, port=None):
        self.port = port
        self.inbox = queue.Queue(maxsize=5000)
        self.last_rx = None
        self.error = None
        self.isotp = obd.IsoTpAssembler()
        self._fw = None
        self._thread = None
        self._awaiting = False
        self._pending_fc = None
        self._stop = threading.Event()        # shuts the device thread down (server exit, tests)
        self.capturing = threading.Event()    # poll the car only while this is set
        self._new_capture = False
        self._streaming = False
        self.on_button = None                 # callback(color) for FREE-WILi button presses
        self.device_view = None               # callable -> {"text": str, "leds": [(r, g, b)] * 7}
        self._buttons_down = set()
        self._shown_text, self._shown_leds, self._view_at = None, [None] * 7, 0.0
        self.bench = BenchEcu(BENCH_SCENARIO) if BENCH_SCENARIO else None
        self._bench_tx = collections.deque()
        self._raw = None
        self._reset_car()

    def _reset_car(self):
        """Forget everything learned about the car (new connection, maybe a new car)."""
        self._codes = {}               # (ecu id, kind) -> [(code, tags)], latest answer
        self._checks = set()           # which kinds of code check the car answered
        self._mil_lamp = None          # from Mode 01 PID 01, when supported
        self.supported_pids = None     # None = unknown yet: poll everything
        self.vin = None
        self._vin_tries = 0
        self.extended = False          # 29-bit CAN IDs (found during discovery)
        self.ecus = set()              # reply IDs of ECUs that answered
        self._freeze = {"code": None, "readings": {}}
        self._mode06 = None            # supported Mode 06 test IDs; None = not asked yet
        self._misfires = {}

    def link(self):
        """Keep the FREE-WILi connected for its screen, LEDs and buttons, even
        when not capturing. Reconnects by itself after an unplug."""
        self._stop.clear()
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="freewili", daemon=True)
            self._thread.start()

    def shutdown(self):
        self._stop.set()

    def start(self):
        while not self.inbox.empty():      # drop anything pushed before capture started
            self.inbox.get_nowait()
        self._open_raw_log()
        self._new_capture = True
        self.capturing.set()
        self.link()

    def stop(self):
        self.capturing.clear()
        raw, self._raw = self._raw, None
        if raw:
            raw.close()

    def send_can(self, can_id, data, extended=False):
        """The ONLY transmit path. obd.check_tx() rejects anything that isn't a
        read-only request (no clearing codes, no writes, no UDS sessions)."""
        obd.check_tx(can_id, data, extended)
        if self._fw is None:
            raise RuntimeError("FREE-WILi is not connected")
        self._log_raw("TX", can_id, data)
        result = self._fw.can_transmit(CAN_CHANNEL, can_id, bytes(data), extended, False)
        if result.is_err():
            raise RuntimeError(f"CAN transmit failed: {result.unwrap_err()}")

    # ---- raw frame log ---------------------------------------------------
    def _open_raw_log(self):
        try:
            RECORDINGS_DIR.mkdir(exist_ok=True)
            path = RECORDINGS_DIR / f"raw-{time.strftime('%Y%m%d-%H%M%S')}.canlog"
            self._raw = path.open("w", buffering=1)
            self._raw.write("# time  dir  id  data   (TX = Otto's request, RX = the car)\n")
        except OSError:
            self._raw = None

    def _log_raw(self, direction, can_id, data):
        raw = self._raw
        if raw:
            try:
                cid = f"{can_id:08X}" if can_id > 0x7FF else f"{can_id:03X}"
                raw.write(f"{time.time():.3f} {direction} {cid} {bytes(data).hex(' ').upper()}\n")
            except ValueError:             # closed by stop() mid-write
                pass

    # ---- device thread -------------------------------------------------
    def _run(self):
        while not self._stop.is_set():
            try:
                self._open()
                self._poll_loop()
            except Exception as e:             # unplugged, wrong firmware, missing package...
                self.error = str(e) or type(e).__name__
                self._stop.wait(RETRY_AFTER_S)
            finally:
                self._close()

    def _open(self):
        try:
            from freewili import FreeWili
        except ImportError:
            raise RuntimeError("Python package missing: pip install freewili")
        devices = [d for d in FreeWili.find_all() if not self.port or self.port in str(d)]
        if not devices:
            raise RuntimeError("No FREE-WILi found. Check the USB cable")
        fw = devices[0]
        result = fw.open()
        if result.is_err():
            raise RuntimeError(f"Can't open FREE-WILi: {result.unwrap_err()}")
        self._fw = fw
        self._reset_car()
        fw.set_event_callback(self._on_event)
        self._streaming = False
        self._shown_text, self._shown_leds = None, [None] * 7
        try:
            fw.enable_button_events(True, 100)    # sample buttons every 100 ms
        except Exception:
            pass                                  # screen firmware without button events: CAN still works
        self.error = None

    def _close(self):
        fw, self._fw = self._fw, None
        if fw is None:
            return
        try:
            self._set_streaming(fw, False)
            fw.enable_button_events(False)
            fw.show_text_display("Otto\nDisconnected")
            for i in range(7):
                fw.set_board_leds(i, 0, 0, 0)
        except Exception:
            pass
        try:
            fw.close()
        except Exception:
            pass

    def _set_streaming(self, fw, on):
        """CAN frames are only streamed to the laptop while capturing."""
        if self._streaming == on or not hasattr(fw, "can_enable_streaming"):
            self._streaming = on
            return
        result = fw.can_enable_streaming(CAN_CHANNEL, on)
        if on and result.is_err():
            raise RuntimeError(f"CAN streaming refused (needs SpartaHack firmware V92): {result.unwrap_err()}")
        if self.bench:
            fw.can_enable_streaming(BENCH_CHANNEL, on)
        self._streaming = on

    def _update_device(self):
        """Mirror Otto's status on the FREE-WILi screen and LEDs (only what changed, ~1/s)."""
        if self.device_view is None or time.monotonic() - self._view_at < 1.0:
            return
        self._view_at = time.monotonic()
        try:
            view = self.device_view()
            if view["text"] != self._shown_text:
                self._fw.show_text_display(view["text"])
                self._shown_text = view["text"]
            for i, rgb in enumerate(view["leds"][:7]):
                if rgb != self._shown_leds[i]:
                    self._fw.set_board_leds(i, *rgb)
                    self._shown_leds[i] = rgb
        except Exception:
            pass                               # the screen is a nice-to-have; never stop reading the car for it

    def _poll_loop(self):
        requests = None
        sent_at = 0.0
        while not self._stop.is_set():
            self._fw.process_events()          # runs _on_event for each received frame / button press
            while self._bench_tx:              # fake car's replies, bench channel only
                can_id, data = self._bench_tx.popleft()
                self._fw.can_transmit(BENCH_CHANNEL, can_id, data, False, False)
            self._update_device()
            if not self.capturing.is_set():
                self._set_streaming(self._fw, False)
                requests, self._awaiting, self._pending_fc = None, False, None
                self._stop.wait(0.02)
                continue
            if requests is None or self._new_capture:   # a fresh capture: rediscover the car
                self._new_capture = False
                self._reset_car()
                self._set_streaming(self._fw, True)
                requests = self._requests()
            if self._pending_fc is not None:   # ECU is mid-reply and waiting for us
                fc_id, self._pending_fc = self._pending_fc, None
                self.send_can(fc_id, obd.FLOW_CONTROL, self.extended)
                self._awaiting = True          # wait for the rest of the reply
                sent_at = time.monotonic()
                continue
            if self._awaiting and time.monotonic() - sent_at < REPLY_TIMEOUT_S:
                continue
            try:
                can_id, data = next(requests)
                self.send_can(can_id, data, self.extended)
            except RuntimeError as e:          # e.g. no ACK: CAN wires not connected
                self.error = str(e)
                self._stop.wait(0.5)
                continue
            self._awaiting = True
            sent_at = time.monotonic()

    def _supports(self, pid):
        return self.supported_pids is None or pid in self.supported_pids

    def _broadcast_id(self):
        return obd.OBD_REQUEST_ID_29 if self.extended else obd.OBD_REQUEST_ID

    def _requests(self):
        """Yields (can_id, data) to send. Replies are handled between yields.

        1. Discovery: who answers, on 11-bit or 29-bit IDs, and which PIDs they support.
        2. Then forever: whatever is most overdue. Codes every DTC_EVERY_S, each
           PID at its own rate (obd.PID_INTERVAL), so slow readings never go stale.
        """
        for extended in (False, False, True, True):        # two tries each, 11-bit first
            self.extended = extended
            yield self._broadcast_id(), obd.build_request(0x01, 0x00)
            if self.ecus:
                break
        else:
            self.extended = False                          # nobody answered: keep trying the usual way
        for base in obd.SUPPORT_PIDS[1:]:
            if not (self.supported_pids and base in self.supported_pids):
                break                                      # each bitmap's last bit says the next exists
            yield self._broadcast_id(), obd.build_request(0x01, base)
        if self.supported_pids:
            keys = sorted(obd.PIDS[p][0] for p in self.supported_pids if p in obd.PIDS)
            self.ingest_frame({"supported": keys}, time.time())

        # [due, every, can_id or None (= broadcast), data]
        codes = [[0.0, DTC_EVERY_S, None, r] for r in DTC_REQUESTS]
        pids = [[0.0, every, None, obd.build_request(0x01, pid)]
                for pid, every in obd.PID_INTERVAL.items() if self._supports(pid)]
        asked_directly = set()
        next_vin = 0.0
        next_deep = time.monotonic() + DEEP_FIRST_S
        while True:
            now = time.monotonic()
            if now >= next_deep:
                next_deep = now + DEEP_EVERY_S
                yield from self._deep_checks()
                continue
            for ecu in sorted(self.ecus - asked_directly):  # fault memory, one ECU at a time
                asked_directly.add(ecu)
                codes.append([0.0, DTC_EVERY_S, obd.physical_id(ecu, self.extended), UDS_FAULT_MEMORY])
            if self.vin is None and self._vin_tries < VIN_TRIES and now >= next_vin:
                self._vin_tries += 1
                next_vin = now + VIN_RETRY_S
                yield self._broadcast_id(), obd.build_request(0x09, 0x02)
                continue
            tasks = codes + pids
            due = [t for t in tasks if t[0] <= now]
            task = min(due or pids or tasks, key=lambda t: t[0])   # nothing due: refresh readings early
            task[0] = now + task[1]
            yield (task[2] or self._broadcast_id()), task[3]

    def _deep_checks(self):
        """Freeze frame (readings saved when a code was set) and misfire counts per cylinder."""
        bcast = self._broadcast_id()
        yield bcast, obd.build_request(0x02, 0x02, 0x00)            # which code caused the freeze frame
        if self._freeze["code"]:
            for pid in obd.FREEZE_PIDS:
                if self._supports(pid):
                    yield bcast, obd.build_request(0x02, pid, 0x00)
        if self._mode06 is None:
            self._mode06 = set()
            yield bcast, obd.build_request(0x06, obd.MISFIRE_SUPPORT_MID)
        for mid in sorted(self._mode06 & set(obd.MISFIRE_MIDS)):
            yield bcast, obd.build_request(0x06, mid)

    def _diag(self, now, **parts):
        self.ingest_frame({"diagnostics": parts, "connection": "connected"}, now)

    def _on_event(self, event_type, frame, data):
        name = getattr(event_type, "name", "")
        if name == "Button":
            self._on_buttons(data)
            return
        if name not in ("CANRX0", "CANRX1"):
            return
        if self.bench and not data.is_extended and data.arb_id not in obd.ECU_RESPONSE_IDS:
            self._bench_tx.extend(self.bench.handle(data.arb_id, data.data, time.time()))   # fake car sees our request
            return
        if not obd.is_response_id(data.arb_id, bool(data.is_extended)):
            return
        self._log_raw("RX", data.arb_id, data.data)
        self.ingest_can(data.arb_id, data.data, time.time())

    def _on_buttons(self, data):
        """Button events are snapshots of which buttons are held; act on each new press."""
        down = {c for c in ("gray", "yellow", "green", "blue", "red") if getattr(data, c, False)}
        pressed, self._buttons_down = down - self._buttons_down, down
        for color in pressed:
            if self.on_button:
                threading.Thread(target=self.on_button, args=(color,), daemon=True).start()

    # ---- shared plumbing -----------------------------------------------
    def ingest_frame(self, frame, now):
        try:
            self.inbox.put_nowait(dict(frame, t=frame.get("t") or now))
        except queue.Full:
            pass
        self.last_rx = now

    def ingest_can(self, can_id, data, now):
        """Decode one raw CAN frame. Returns what was decoded (for debugging)."""
        extended = can_id > 0x7FF
        if not obd.is_response_id(can_id, extended):
            return None
        payload, send_fc = self.isotp.feed(can_id, data)
        if send_fc and self._fw is not None:
            self._pending_fc = obd.flow_control_id(can_id, extended)
        if not payload:
            return None
        self._awaiting = False                 # a complete answer: ready for the next request
        if payload[0] != 0x7F:                 # a real answer (not "can't do that"): this ECU is here
            self.ecus.add(can_id)
        service = payload[0]
        if service == 0x41:
            supported = obd.parse_supported(payload)
            if supported is not None:
                self.supported_pids = (self.supported_pids or set()) | supported
                return {"supported_pids": sorted(supported)}
            monitor = obd.parse_monitor(payload)
            if monitor is not None:
                self._mil_lamp = monitor[0]
                self.ingest_frame({"mil": monitor[0], "connection": "connected"}, now)
                readiness = obd.parse_readiness(payload)
                if readiness:
                    self._diag(now, readiness=readiness)
                return {"mil": monitor[0], "obd_code_count": monitor[1]}
            decoded = obd.parse_mode01(data)   # every PID we poll fits in one frame
            if decoded:
                key, value = decoded
                self.ingest_frame({"readings": {key: value}, "connection": "connected"}, now)
                return {key: value}
        if service in obd.DTC_LIST_REPLIES:
            kind = obd.DTC_LIST_REPLIES[service]
            codes = obd.parse_dtc_list(payload)
            if codes is not None:
                self._update_codes(can_id, kind, [(c, [kind]) for c in codes], now)
                return {"trouble_codes": codes}
        if service == 0x59:
            found = obd.parse_uds_dtcs(payload)
            if found is not None:
                self._update_codes(can_id, "uds", found, now)
                return {"fault_memory": found}
        if service == 0x41 and len(payload) > 1 and payload[1] == obd.FUEL_STATUS_PID:
            status = obd.parse_fuel_status(payload)
            if status:
                self._diag(now, fuel_status=status)
                return {"fuel_status": status}
        if service == 0x42:
            item = obd.parse_freeze(payload)
            if item:
                key, value = item
                if key == "code":
                    if value != self._freeze["code"]:
                        self._freeze = {"code": value, "readings": {}}
                else:
                    self._freeze["readings"][key] = value
                self._diag(now, freeze_frame={"code": self._freeze["code"], "readings": dict(self._freeze["readings"])})
                return {"freeze_frame": item}
        if service == 0x46:
            parsed = obd.parse_mode06(payload)
            if parsed and parsed[0] == "support":
                self._mode06 = (self._mode06 or set()) | parsed[1]
            elif parsed:
                for cyl, counts in obd.misfire_counts(parsed[1]).items():
                    self._misfires[cyl] = {**self._misfires.get(cyl, {}), **counts}
                if self._misfires:
                    self._diag(now, misfires={str(c): v for c, v in sorted(self._misfires.items())})
            return {"mode06": parsed}
        if service == 0x49:
            vin = obd.parse_vin(payload)
            if vin and vin != self.vin:
                self.vin = vin
                info = vin_decoder.decode_offline(vin)
                self.ingest_frame({"vin": vin, "vehicle": vin_decoder.vehicle_name(info),
                                   "connection": "connected"}, now)
                if vin_decoder.LOOKUP_ONLINE:      # exact model + trim, without blocking the CAN loop
                    threading.Thread(target=self._lookup_vin, args=(vin,), daemon=True).start()
                return {"vin": vin}
        return None

    def _lookup_vin(self, vin):
        name = vin_decoder.vehicle_name(vin_decoder.decode(vin))
        if name and vin == self.vin:
            self.ingest_frame({"vin": vin, "vehicle": name}, time.time())

    def _update_codes(self, can_id, kind, found, now):
        """Engine and transmission ECUs answer separately; merge all their answers."""
        self._codes[(can_id, kind)] = found
        self._checks.add(CHECK_NAMES[kind])
        tags = collections.defaultdict(set)
        for entries in self._codes.values():
            for code, t in entries:
                tags[code].update(t)
        status = {c: [t for t in TAG_ORDER if t in ts] for c, ts in sorted(tags.items())}
        # Lamp state only from the car itself (PID 01). Codes alone don't mean the
        # lamp is on, and guessing would be a false alert. None = not reported.
        mil = self._mil_lamp
        self.ingest_frame({"trouble_codes": list(status), "code_status": status,
                           "code_checks": sorted(self._checks), "mil": mil, "connection": "connected"}, now)

    def status(self, now):
        if self.last_rx is None or now - self.last_rx > NO_DATA_AFTER_S:
            if self.error:
                return "disconnected", self.error
        if self.last_rx is None:
            return "waiting", "Plug in the FREE-WILi to start"
        if now - self.last_rx > NO_DATA_AFTER_S:
            return "disconnected", "Lost the car — check the plug and that the key is ON"
        if self.bench:
            return "connected", f"BENCH: fake car on Neptune channel B ({self.bench.scenario}), not a real vehicle"
        return "connected", "Reading your car"

    def poll(self, now):
        merged = None
        while True:
            try:
                f = self.inbox.get_nowait()
            except queue.Empty:
                break
            if merged is None:
                merged = {"readings": {}}
            merged["readings"].update(f.get("readings") or {})
            if f.get("diagnostics"):
                merged.setdefault("diagnostics", {}).update(f["diagnostics"])
            for k in FRAME_KEYS:
                if f.get(k) is not None:
                    merged[k] = f[k]
            merged["t"] = f["t"]
        return merged
