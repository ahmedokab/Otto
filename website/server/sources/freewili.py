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

SAFETY: this project is READ-ONLY. Every transmit goes through send_can(),
which calls obd.check_tx(). Never add another path that writes to the bus.

Env:
  FREEWILI_CAN_CHANNEL  Neptune channel wired to the car (default 0 = channel A,
                        DB15 pins 6/14, GND 8)
  FREEWILI_PORT         serial number to pick when several FREE-WILis are plugged in
"""
import itertools
import os
import queue
import threading
import time

from .base import Source
from .. import obd

NO_DATA_AFTER_S = 2.0
CAN_CHANNEL = int(os.getenv("FREEWILI_CAN_CHANNEL", "0"))
REPLY_TIMEOUT_S = 0.1      # move on if no ECU answers a request
DTC_EVERY_S = 5.0          # trouble codes change rarely
RETRY_AFTER_S = 2.0        # reopen the device after unplug / error


class FreeWiliSource(Source):
    mode = "live"

    def __init__(self, port=None):
        self.port = port
        self.inbox = queue.Queue(maxsize=5000)
        self.last_rx = None
        self.error = None
        self.isotp = obd.IsoTpAssembler()
        self._codes = {}               # latest Mode 03 answer per ECU
        self._fw = None
        self._thread = None
        self._awaiting = False
        self._pending_fc = None
        self._stop = threading.Event()

    def start(self):
        self._stop.clear()
        while not self.inbox.empty():      # drop anything pushed before capture started
            self.inbox.get_nowait()
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="freewili-can", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()

    def send_can(self, can_id, data):
        """The ONLY transmit path. obd.check_tx() rejects anything that isn't a
        read-only request (no clearing codes, no writes, no UDS sessions)."""
        obd.check_tx(can_id, data)
        if self._fw is None:
            raise RuntimeError("FREE-WILi is not connected")
        result = self._fw.can_transmit(CAN_CHANNEL, can_id, bytes(data), False, False)
        if result.is_err():
            raise RuntimeError(f"CAN transmit failed: {result.unwrap_err()}")

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
        fw.set_event_callback(self._on_event)
        result = fw.can_enable_streaming(CAN_CHANNEL, True)
        if result.is_err():
            raise RuntimeError(f"CAN streaming refused (needs SpartaHack firmware V92): {result.unwrap_err()}")
        self.error = None

    def _close(self):
        fw, self._fw = self._fw, None
        if fw is None:
            return
        try:
            fw.can_enable_streaming(CAN_CHANNEL, False)
            fw.close()
        except Exception:
            pass

    def _poll_loop(self):
        requests = self._requests()
        sent_at = 0.0
        while not self._stop.is_set():
            self._fw.process_events()          # runs _on_event for each received frame
            if self._pending_fc is not None:   # ECU is mid-reply and waiting for us
                fc_id, self._pending_fc = self._pending_fc, None
                self.send_can(fc_id, obd.FLOW_CONTROL)
                sent_at = time.monotonic()
                continue
            if self._awaiting and time.monotonic() - sent_at < REPLY_TIMEOUT_S:
                continue
            try:
                self.send_can(obd.OBD_REQUEST_ID, next(requests))
            except RuntimeError as e:          # e.g. no ACK: CAN wires not connected
                self.error = str(e)
                self._stop.wait(0.5)
                continue
            self._awaiting = True
            sent_at = time.monotonic()

    @staticmethod
    def _requests():
        """Fast PIDs every round, one slow PID per round, codes every few seconds."""
        slow = itertools.cycle(obd.SLOW_PIDS)
        next_dtc = 0.0
        while True:
            for pid in obd.FAST_PIDS:
                yield obd.build_request(0x01, pid)
            yield obd.build_request(0x01, next(slow))
            if time.monotonic() >= next_dtc:
                next_dtc = time.monotonic() + DTC_EVERY_S
                yield obd.build_request(0x03)

    def _on_event(self, event_type, frame, data):
        if getattr(event_type, "name", "") not in ("CANRX0", "CANRX1") or data.is_extended:
            return
        if data.arb_id in obd.ECU_RESPONSE_IDS:
            self._awaiting = False
        self.ingest_can(data.arb_id, data.data, time.time())

    # ---- shared plumbing -----------------------------------------------
    def ingest_frame(self, frame, now):
        try:
            self.inbox.put_nowait(dict(frame, t=frame.get("t") or now))
        except queue.Full:
            pass
        self.last_rx = now

    def ingest_can(self, can_id, data, now):
        """Decode one raw CAN frame. Returns what was decoded (for debugging)."""
        if can_id not in obd.ECU_RESPONSE_IDS:
            return None
        payload, send_fc = self.isotp.feed(can_id, data)
        if send_fc and self._fw is not None:
            self._pending_fc = obd.flow_control_id(can_id)
        if not payload:
            return None
        if payload[0] == 0x41:
            decoded = obd.parse_mode01(data)   # every PID we poll fits in one frame
            if decoded:
                key, value = decoded
                self.ingest_frame({"readings": {key: value}, "connection": "connected"}, now)
                return {key: value}
        if payload[0] == 0x43:
            codes = obd.parse_mode03(payload)
            if codes is not None:
                self._codes[can_id] = codes    # engine and transmission ECUs answer separately
                merged = sorted({c for cs in self._codes.values() for c in cs})
                self.ingest_frame({"trouble_codes": merged, "mil": bool(merged), "connection": "connected"}, now)
                return {"trouble_codes": codes}
        return None

    def status(self, now):
        if self.last_rx is None or now - self.last_rx > NO_DATA_AFTER_S:
            if self.error:
                return "disconnected", self.error
        if self.last_rx is None:
            return "waiting", "Plug in the FREE-WILi to start"
        if now - self.last_rx > NO_DATA_AFTER_S:
            return "disconnected", "Lost the car — check the plug and that the key is ON"
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
            for k in ("trouble_codes", "mil", "connection"):
                if f.get(k) is not None:
                    merged[k] = f[k]
            merged["t"] = f["t"]
        return merged
