"""Genere le miroir local du jeu de donnees (Parquet partitionne par departement) et la
verite terrain `expected.json`. Deterministe (graine fixe). Relancer apres modification :
    python tasks/t01_s3_parquet/tools/make_fixture.py
Les valeurs sont synthetiques : aucune commune reelle, rien a memoriser."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "fixtures" / "data" / "census"
DEPS = ["01", "13", "2A", "31", "33", "44", "59", "69", "75", "97"]
SYLL = ["Val", "Mont", "Saint", "Bel", "Roche", "Font", "Ville", "Cham", "Bois", "Pont",
        "Aub", "Mar", "Ver", "Cler", "Lus", "Or", "Bri", "Fer", "Tour", "Bre"]
SUFF = ["mont", "val", "ac", "ay", "euil", "ville", "y", "court", "ières", "as", "ac-le-Haut",
        "-sur-Lisle", "-les-Bains", "ol", "an", "ette"]


def main() -> None:
    rng = np.random.default_rng(20260902)
    frames = []
    names = set()
    for dep in DEPS:
        n = int(rng.integers(90, 160))
        rows = []
        for i in range(n):
            while True:
                name = rng.choice(SYLL) + rng.choice(SYLL).lower() + rng.choice(SUFF)
                if name not in names:
                    names.add(name)
                    break
            pop = int(np.exp(rng.normal(7.8, 1.1)))
            if rng.random() < 0.02:
                pop = int(pop * rng.integers(20, 60))  # quelques grandes communes
            rev = round(float(rng.normal(21500, 2800)), 1)
            if rng.random() < 0.02:
                rev = None  # revenu manquant : la mediane doit l'ignorer
            rows.append({"code_commune": f"{dep}{i:03d}", "commune": name,
                         "departement": dep, "population": pop, "revenu_disponible": rev})
        frames.append(pd.DataFrame(rows))
    df = pd.concat(frames, ignore_index=True)
    df["revenu_disponible"] = df["revenu_disponible"].astype("float64")

    if OUT.exists():
        for p in OUT.rglob("*"):
            if p.is_file():
                p.unlink()
    for dep, part in df.groupby("departement"):
        d = OUT / f"dep={dep}"
        d.mkdir(parents=True, exist_ok=True)
        part.to_parquet(d / "part-0.parquet", index=False)

    medians = df.groupby("departement")["revenu_disponible"].median().round(1)
    top10 = df.nlargest(10, "population")[["commune", "population"]]
    expected = {
        "n_rows": len(df),
        "median_revenu_by_departement": {k: float(v) for k, v in medians.items()},
        "top10_communes": [{"commune": r.commune, "population": int(r.population)}
                           for r in top10.itertuples()],
    }
    (HERE / "expected.json").write_text(json.dumps(expected, indent=2, ensure_ascii=False),
                                        encoding="utf-8")
    print(f"{len(df)} lignes, {len(DEPS)} partitions -> {OUT}")


if __name__ == "__main__":
    main()
