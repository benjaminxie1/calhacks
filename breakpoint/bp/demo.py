"""`make demo`: build the world, replay the quarter with scripted people, and check
that every case ends in its expected state against the answer key (T36)."""
from __future__ import annotations

import sys
import time

from bp import config
from bp.db import engine
from bp.engine.replay import Replay
from bp.eval import evaluate


def run(eng=None, seed: int = config.SEED, quiet: bool = False) -> dict:
    rp = Replay(eng=eng or engine(), seed=seed, controller_mode="scripted", teach_mode="scripted")
    rp.init(fresh=True)
    t0 = time.time()
    rp.run()
    with rp.eng.connect() as c:
        r = evaluate(c, rp.store)
    r["secs"] = round(time.time() - t0, 1)
    if not quiet:
        res = r["resolution"]
        print(f"replayed {len(rp.store.answer_key)} planted breaks in {r['secs']}s")
        print(f"detection recall {r['detection']['recall']}, precision {r['detection']['precision']}")
        print(f"resolution {res['correct']}/{res['total']} in the expected final state with answer-key amounts")
        print(f"ledger equals truth: {r['ledger_vs_truth']['equal']}; invariants green: {r['invariants']['all_green']}")
    return r


def ok(r: dict) -> bool:
    return (r["resolution"]["correct"] == r["resolution"]["total"] and r["ledger_vs_truth"]["equal"]
            and r["invariants"]["all_green"] and not r["detection"]["false_positives"])


if __name__ == "__main__":
    sys.exit(0 if ok(run()) else 1)
