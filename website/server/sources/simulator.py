"""Simulated vehicle with fault scenarios. Always clearly labeled SIMULATOR in the UI."""
import math
import random

from .base import Source

SCENARIOS = {
    "healthy":     {"label": "Healthy commute",      "code": None,    "description": "Warm engine, normal driving, no faults."},
    "lean":        {"label": "Lean running",         "code": "P0171", "description": "Vacuum-leak style lean condition. Fuel trims climb until the ECU sets P0171."},
    "misfire":     {"label": "Misfire",              "code": "P0300", "description": "Rough running from random misfires. ECU sets P0300."},
    "overheat":    {"label": "Overheating",          "code": "P0217", "description": "Coolant temperature climbs past the safe limit. ECU sets P0217."},
    "low_voltage": {"label": "Charging problem",     "code": "P0562", "description": "Alternator not keeping up; system voltage sags. ECU sets P0562."},
    "thermostat":  {"label": "Thermostat stuck open", "code": "P0128", "description": "Engine never warms up fully. ECU sets P0128."},
}


class SimulatorSource(Source):
    mode = "simulator"

    def __init__(self, scenario="healthy", seed=None):
        self.rng = random.Random(seed)
        self.set_scenario(scenario)

    def set_scenario(self, scenario):
        self.scenario = scenario if scenario in SCENARIOS else "healthy"
        self.t0 = None
        self.latched = set()   # real DTCs stay set once detected

    def start(self):
        self.t0 = None
        self.latched = set()

    def status(self, now):
        return "connected", f"Simulated vehicle · {SCENARIOS[self.scenario]['label']}"

    def poll(self, now):
        if self.t0 is None:
            self.t0 = now
        t = now - self.t0
        g = self.rng.gauss

        speed = max(0.0, 38 + 34 * math.sin(t / 9.0))
        rpm = 760 + speed * 27 + g(0, 8)
        throttle = max(0.0, 6 + speed * 0.45 + g(0, 0.8))
        coolant = 90 - 18 * math.exp(-t / 12) + g(0, 0.2)
        volts = 14.1 + g(0, 0.04)
        stft = g(0, 1.5)
        ltft = 1.5 + g(0, 0.3)
        maf = 2.5 + rpm * 0.0035 + throttle * 0.15 + g(0, 0.1)
        iat = 27 + g(0, 0.2)

        s = self.scenario
        if s == "lean":
            ltft = min(18.0, 3 + t * 0.9) + g(0, 0.3)
            stft = 6 + g(0, 1.5)
            maf *= 0.92
            if ltft > 14:
                self.latched.add("P0171")
        elif s == "misfire":
            rpm += g(0, 70) + (-140 if self.rng.random() < 0.12 else 0)
            if t > 6:
                self.latched.add("P0300")
        elif s == "overheat":
            coolant = 90 + min(30.0, max(0.0, t - 3) * 1.2) + g(0, 0.2)
            if coolant > 113:
                self.latched.add("P0217")
        elif s == "low_voltage":
            volts = 13.2 - min(1.7, t * 0.08) + g(0, 0.04)
            if volts < 11.9:
                self.latched.add("P0562")
        elif s == "thermostat":
            coolant = 62 - 30 * math.exp(-t / 10) + g(0, 0.2)
            if t > 15:
                self.latched.add("P0128")

        codes = sorted(self.latched)
        return {
            "t": now,
            "connection": "connected",
            "readings": {
                "rpm": round(rpm),
                "speed_kph": round(speed),
                "coolant_c": round(coolant, 1),
                "ecu_voltage_v": round(volts, 2),
                "throttle_pct": round(throttle, 1),
                "stft_pct": round(stft, 1),
                "ltft_pct": round(ltft, 1),
                "maf_gs": round(maf, 1),
                "intake_c": round(iat, 1),
            },
            "trouble_codes": codes,
            "mil": bool(codes),
        }
