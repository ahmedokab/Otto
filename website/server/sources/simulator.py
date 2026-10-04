"""Simulated vehicle with fault scenarios. Always clearly labeled SIMULATOR in the UI.

Built to feel like a real car read over OBD-II, not a random-number generator:
  * Each reading updates at the same pace Otto polls a real car (obd.KEY_INTERVAL):
    rpm/speed/throttle ~0.6 s, temperatures and trims ~2.5 s, slow values 5-15 s.
  * Values drift smoothly (a slow random walk), never jump sample to sample.
  * A looping 2-minute drive: idle, pull away, cruise, slow down, stop at a light.
  * Temperatures warm up over minutes; fuel level falls slowly.
"""
import math
import random

from .base import Source
from ..obd import CONTINUOUS_TESTS, FUEL_STATUS, KEY_INTERVAL

SCENARIOS = {
    "healthy":     {"label": "Healthy commute",      "code": None,    "description": "Warm engine, normal driving, no faults."},
    "lean":        {"label": "Lean running",         "code": "P0171", "description": "Vacuum-leak style lean condition. Fuel trims climb until the ECU sets P0171."},
    "misfire":     {"label": "Misfire",              "code": "P0300", "description": "Rough running from random misfires. ECU sets P0300."},
    "overheat":    {"label": "Overheating",          "code": "P0217", "description": "Coolant temperature climbs past the safe limit. ECU sets P0217."},
    "low_voltage": {"label": "Charging problem",     "code": "P0562", "description": "Alternator not keeping up; system voltage sags. ECU sets P0562."},
    "thermostat":  {"label": "Thermostat stuck open", "code": "P0128", "description": "Engine never warms up fully. ECU sets P0128."},
}

# Drive cycle keyframes: (seconds, km/h), looped
DRIVE = [(0, 0), (12, 0), (24, 38), (34, 55), (60, 62), (78, 58), (90, 20), (98, 0), (108, 0), (120, 0)]
CYCLE_S = DRIVE[-1][0]
GEARS = [(20, 68), (38, 44), (56, 31), (80, 25), (999, 21)]      # (up to km/h, rpm per km/h)
CODE_EVERY_S = 5.0
FREEZE_KEYS = ["rpm", "speed_kph", "coolant_c", "load_pct", "stft_pct", "ltft_pct", "map_kpa", "throttle_pct", "intake_c"]
READINESS = CONTINUOUS_TESTS + ["Catalytic converter", "Fuel vapor (EVAP)", "Oxygen sensors", "Oxygen sensor heaters"]
REPORTED = ["rpm", "speed_kph", "throttle_pct", "load_pct", "coolant_c", "ecu_voltage_v", "stft_pct", "ltft_pct",
            "maf_gs", "intake_c", "map_kpa", "timing_deg", "pedal_d_pct", "cmd_throttle_pct", "oil_c", "cat_c",
            "lambda", "cmd_lambda", "o2_down_v", "fuel_pct", "ambient_c", "baro_kpa", "runtime_min"]


def _speed_at(t):
    t %= CYCLE_S
    for (t0, v0), (t1, v1) in zip(DRIVE, DRIVE[1:]):
        if t0 <= t <= t1:
            k = (t - t0) / (t1 - t0) if t1 > t0 else 0
            return v0 + (v1 - v0) * (1 - math.cos(math.pi * k)) / 2     # smooth start and stop
    return 0.0


class SimulatorSource(Source):
    mode = "simulator"

    def __init__(self, scenario="healthy", seed=None):
        self.rng = random.Random(seed)
        self.set_scenario(scenario)

    def set_scenario(self, scenario):
        self.scenario = scenario if scenario in SCENARIOS else "healthy"
        self.start()

    def start(self):
        self.t0 = None
        self.latched = set()       # real DTCs stay set once detected
        self.walk = {}             # slow random drift per reading
        self.last_t = None
        self.sent = {}             # reading -> last time it was reported
        self.codes_sent = -1e9
        self.first = True
        self._t = 0.0
        self.freeze = None         # readings saved when the first code set (like a real ECU)
        self.misfire_count = [0.0, 0.0, 0.0, 0.0]

    def status(self, now):
        return "connected", f"Simulated vehicle · {SCENARIOS[self.scenario]['label']}"

    def _drift(self, key, sigma, tau, dt):
        """Ornstein-Uhlenbeck drift: wanders by about ±sigma, changes over ~tau seconds."""
        x = self.walk.get(key, 0.0)
        x += -x / tau * dt + sigma * math.sqrt(2 * dt / tau) * self.rng.gauss(0, 1)
        self.walk[key] = x
        return x

    def values(self, now):
        """Every reading's current value (no pacing). The bench ECU uses this too."""
        if self.t0 is None:
            self.t0 = self.last_t = now
        t, dt = now - self.t0, max(0.0, min(1.0, now - self.last_t))
        self.last_t = now
        d = lambda key, sigma, tau=6.0: self._drift(key, sigma, tau, dt)

        speed = max(0.0, _speed_at(t) + (d("speed", 1.5) if _speed_at(t) > 5 else 0))
        accel = (_speed_at(t + 1) - _speed_at(t - 1)) / 2                    # km/h per second
        idle = speed < 2
        ratio = next(r for top, r in GEARS if speed <= top)
        rpm = 760 + d("idle", 12, 4) if idle else max(1100.0, speed * ratio) + max(0.0, accel) * 60 + d("rpm", 25)
        pedal = 0.0 if idle else max(0.0, min(80.0, 12 + accel * 6 + speed * 0.12 + d("pedal", 1.5)))
        throttle = 13 + (0.0 if idle else pedal * 0.55) + d("thr", 0.4)
        load = 21 + (0.0 if idle else pedal * 0.6 + speed * 0.08) + d("load", 1.0)
        warm = 1 - math.exp(-t / 150)                                         # minutes to warm up
        coolant = 52 + 38 * warm + d("cool", 0.4, 20)
        oil = 48 + 40 * (1 - math.exp(-t / 260)) + d("oil", 0.4, 30)
        volts = 14.1 + d("volt", 0.05, 15)
        stft = d("stft", 2.0, 4)
        ltft = 1.5 + d("ltft", 0.3, 40)
        maf = 2.6 + rpm * 0.0032 + load * 0.08 + d("maf", 0.15)
        iat = 24 + 6 * warm + d("iat", 0.5, 30)
        mapk = 31 + (0.0 if idle else load * 0.9) + d("map", 1.0)
        timing = 6 + (0.0 if idle else 14 + speed * 0.1 - max(0.0, accel) * 1.5) + d("tim", 1.0, 3)
        cat = 380 + 180 * warm + (0.0 if idle else load * 1.5) + d("cat", 6, 30)
        lam = 1.0 + d("lam", 0.008, 2)
        o2r = 0.66 + d("o2r", 0.03, 10)
        fuel = max(5.0, 62 - t * 0.004)

        s = self.scenario
        if s == "lean":
            ltft = min(18.0, 3 + t * 0.6) + d("ltft2", 0.3, 20)
            stft = 6 + d("stft2", 1.5, 4)
            maf *= 0.92
            lam = 1.03 + d("lam2", 0.01, 3)
            if ltft > 14:
                self.latched.add("P0171")
        elif s == "misfire":
            rpm += self.rng.gauss(0, 55) + (-140 if self.rng.random() < 0.15 else 0)   # real roughness
            if t > 8:
                self.latched.add("P0300")
        elif s == "overheat":
            coolant = 90 + min(30.0, max(0.0, t - 5) * 0.5) + d("cool2", 0.3, 20)
            if coolant > 113:
                self.latched.add("P0217")
        elif s == "low_voltage":
            volts = 13.2 - min(1.7, t * 0.03) + d("volt2", 0.04, 10)
            if volts < 11.9:
                self.latched.add("P0562")
        elif s == "thermostat":
            coolant = 62 - 25 * math.exp(-t / 60) + d("cool3", 0.3, 20)
            if t > 40:
                self.latched.add("P0128")

        if s == "misfire":                             # cylinder 3 misfires, others almost never
            self.misfire_count[2] += dt * 1.8
            self.misfire_count[0] += dt * 0.02
        values = {
            "rpm": round(rpm), "speed_kph": round(speed), "throttle_pct": round(throttle, 1), "load_pct": round(load),
            "coolant_c": round(coolant), "ecu_voltage_v": round(volts, 2), "stft_pct": round(stft, 1),
            "ltft_pct": round(ltft, 1), "maf_gs": round(maf, 1), "intake_c": round(iat), "map_kpa": round(mapk),
            "timing_deg": round(timing, 1), "pedal_d_pct": round(pedal), "cmd_throttle_pct": round(max(0.0, throttle - 9)),
            "oil_c": round(oil), "cat_c": round(cat), "lambda": round(lam, 2), "cmd_lambda": 1.0,
            "o2_down_v": round(o2r, 2), "fuel_pct": round(fuel), "ambient_c": 18, "baro_kpa": 99,
            "runtime_min": round(t / 60, 1),
        }
        if self.latched and self.freeze is None:
            self.freeze = {"code": sorted(self.latched)[0], "readings": {k: values[k] for k in FREEZE_KEYS}}
        self._t = t
        return values

    def diagnostics(self):
        """Readiness, fuel control, misfires and freeze frame, as a real car reports them."""
        warming = self._t < 60 or self.scenario == "thermostat"
        status = 1 if warming else 2
        not_ready = {"Catalytic converter"} if self.scenario == "thermostat" else set()
        diag = {
            "fuel_status": {"code": status, "text": FUEL_STATUS[status]},
            "readiness": [{"name": n, "ready": n not in not_ready} for n in READINESS],
            "misfires": {str(i + 1): {"recent": round(c), "last_drive": round(c)} for i, c in enumerate(self.misfire_count)},
        }
        if self.freeze:
            diag["freeze_frame"] = self.freeze
        return diag

    def poll(self, now):
        values = self.values(now)
        frame = {"t": now, "connection": "connected"}
        if self.first:                         # what a real car tells Otto when it connects
            self.first = False
            frame["supported"] = list(REPORTED)
        due = {k: v for k, v in values.items() if now - self.sent.get(k, -1e9) >= KEY_INTERVAL.get(k, 0.6)}
        for k in due:
            self.sent[k] = now
        frame["readings"] = due
        if now - self.codes_sent >= CODE_EVERY_S:   # codes are checked every few seconds, like Live mode
            self.codes_sent = now
            codes = sorted(self.latched)
            frame.update(trouble_codes=codes, mil=bool(codes),
                         code_status={c: ["stored"] for c in codes},
                         code_checks=["manufacturer", "pending", "permanent", "stored"],
                         diagnostics=self.diagnostics())
        return frame if (due or "trouble_codes" in frame or "supported" in frame) else None
