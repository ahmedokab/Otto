"""LIVE source: real vehicle data via FREE-WILi + your CAN interface.

Two ways for the hardware team to feed the dashboard — pick whichever is
faster for you. Both end up in the same queue, so the UI doesn't care.

  A) HTTP push (any language, separate process — easiest):
       POST /api/ingest       {"readings": {"rpm": 1726}, "trouble_codes": [...]}
       POST /api/ingest/can   {"id": "0x7E8", "data": "04410C1AF8000000"}
     /api/ingest/can runs the raw frame through obd.py for you.

  B) In-process: implement start()/_reader() below to talk to the device
     over USB serial directly and call self.ingest_can(...) for each frame.

SAFETY: this project is READ-ONLY. Every transmit goes through send_can(),
which calls obd.check_tx(). Never add another path that writes to the bus.

Guiding questions for option B (work these out before writing code):
  1. What does one received CAN frame look like on the FREE-WILi serial
     console? (Capture a raw line first. Write it down in the README.)
  2. How do you SEND a frame (0x7DF, build_request(0x01, 0x0C))? Can you see
     the ECU's 0x7E8 reply in the console within ~50 ms?
  3. Polling: if you request 9 PIDs every cycle and each reply takes ~20 ms,
     what update rate do you get? Which PIDs deserve the fast lane (see
     obd.FAST_PIDS)?
  4. Mode 03 with >2 codes arrives as an ISO-TP multi-frame (first frame
     0x1N, then you must send a flow-control 0x30 00 00). How will you
     reassemble it before calling obd.parse_mode03?
  5. What should the UI show when the USB cable is pulled mid-capture?
     (Hint: stop pushing frames — freshness tracking does the rest.)
"""
import queue
import threading

from .base import Source
from .. import obd

NO_DATA_AFTER_S = 2.0


class FreeWiliSource(Source):
    mode = "live"

    def __init__(self, port=None):
        self.port = port
        self.inbox = queue.Queue(maxsize=5000)
        self.last_rx = None
        self._stop = threading.Event()

    # ---- option B: implement these -------------------------------------
    def start(self):
        self._stop.clear()
        while not self.inbox.empty():      # drop anything pushed before capture started
            self.inbox.get_nowait()
        # TODO(hardware): open self.port with pyserial and start a thread
        # running self._reader(). Until then, use option A (HTTP push).

    def stop(self):
        self._stop.set()

    def send_can(self, can_id, data):
        """The ONLY transmit path. obd.check_tx() rejects anything that isn't a
        read-only request (no clearing codes, no writes, no UDS sessions)."""
        obd.check_tx(can_id, data)
        # TODO(hardware): write the frame to the FREE-WILi here.
        raise NotImplementedError

    def _reader(self):
        # TODO(hardware): loop until self._stop is set:
        #   - self.send_can(0x7DF, obd.build_request(...)) for the next PID
        #     (or transmit nothing at all: passive listen-only is even safer)
        #   - read lines, parse (can_id, data) and call self.ingest_can(...)
        pass

    # ---- shared plumbing (already working) -----------------------------
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
        decoded = obd.parse_mode01(data)
        if decoded:
            key, value = decoded
            self.ingest_frame({"readings": {key: value}, "connection": "connected"}, now)
            return {key: value}
        if len(data) > 1 and (data[0] >> 4) == 0 and data[1] == 0x43:
            codes = obd.parse_mode03(data[1:1 + (data[0] & 0x0F)])
            if codes is not None:
                self.ingest_frame({"trouble_codes": codes, "mil": bool(codes), "connection": "connected"}, now)
                return {"trouble_codes": codes}
        return None

    def status(self, now):
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
