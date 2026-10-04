"""Generate sample recordings from the simulator so Replay works out of the box.
Run from project root: python -m tools.make_sample_recordings"""
import json
from server.sources.simulator import SimulatorSource
from server.sources.replay import RECORDINGS_DIR

for scenario, seconds in [("lean", 45), ("overheat", 45)]:
    sim = SimulatorSource(scenario, seed=7)
    path = RECORDINGS_DIR / f"sample-{scenario}.jsonl"
    with path.open("w") as f:
        f.write(json.dumps({"_meta": {"mode": "simulator", "scenario": scenario, "note": "generated sample"}}) + "\n")
        t = 1_759_500_000.0
        for _ in range(int(seconds / 0.2)):
            f.write(json.dumps(sim.poll(t)) + "\n")
            t += 0.2
    print("wrote", path)
