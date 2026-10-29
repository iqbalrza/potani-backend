"""RQ1: stabilitas kandidat teratas Lapisan A terhadap noise sebesar MAE sensor.

Untuk setiap bacaan, input diberi noise seragam [-MAE, +MAE] per parameter sebanyak --trials kali,
lalu dihitung persentase percobaan yang mengubah kandidat teratas (bandingkan dengan baseline KNN 76%).

    python -m app.ml.noise_stability                 # bacaan valid dari DATABASE_URL
    python -m app.ml.noise_stability --csv data.csv  # kolom: ph,moisture,temperature[,light_level]
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import statistics

from app.config import get_settings
from app.domain.catalog import PARAMS
from app.domain.suitability import run_suitability


def top_candidate(reading: dict, light: str | None) -> str | None:
    c = run_suitability(reading, light)["candidates"]
    return c[0]["crop_id"] if c else None


def stability(readings: list[tuple[dict, str | None]], trials: int, seed: int) -> dict:
    s = get_settings()
    rng = random.Random(seed)
    per_reading = []
    for reading, light in readings:
        base = top_candidate(reading, light)
        changed = 0
        for _ in range(trials):
            noisy = {p: (reading[p] + rng.uniform(-s.mae(p), s.mae(p))) if reading.get(p) is not None else None
                     for p in PARAMS}
            if top_candidate(noisy, light) != base:
                changed += 1
        per_reading.append(changed / trials)
    return {
        "n_readings": len(readings),
        "trials_per_reading": trials,
        "mae": {p: s.mae(p) for p in PARAMS},
        "top_change_rate_mean": round(statistics.mean(per_reading), 4) if per_reading else None,
        "top_change_rate_median": round(statistics.median(per_reading), 4) if per_reading else None,
        "crops_file": s.crops_file,
    }


def load_db() -> list[tuple[dict, str | None]]:
    from sqlalchemy import select

    from app.db.models import Measurement, Pot
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        rows = db.execute(select(Measurement, Pot.light_level).outerjoin(Pot, Pot.id == Measurement.pot_id)
                          .where(Measurement.invalid.is_(False))).all()
    return [({p: getattr(m, p) for p in PARAMS}, light) for m, light in rows]


def load_csv(path: str) -> list[tuple[dict, str | None]]:
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append(({p: float(row[p]) if row.get(p) not in (None, "") else None for p in PARAMS},
                        row.get("light_level") or None))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv")
    ap.add_argument("--trials", type=int, default=200)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    readings = load_csv(args.csv) if args.csv else load_db()
    print(json.dumps(stability(readings, args.trials, args.seed), indent=2))


if __name__ == "__main__":
    main()
