"""Genere fixtures/chart.png et expected.json. Communes et valeurs FICTIVES : un modele
texte ne peut pas repondre de memoire (les anciennes valeurs etaient les vraies populations
de Paris/Marseille/Lyon...). Relancer : python tasks/t11_vision_chart/tools/make_chart.py"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent.parent
DATA = [("Valmoutier", 128), ("Saint-Orens-le-Haut", 342), ("Brizeval", 57),
        ("Monclery", 811), ("Aubrac-sur-Lisle", 203), ("Ferrandes", 466)]


def main() -> None:
    names = [d[0] for d in DATA]
    vals = [d[1] for d in DATA]
    fig, ax = plt.subplots(figsize=(9, 5.5), dpi=100)
    bars = ax.bar(names, vals, color="#4060a0")
    ax.set_title("Population par commune (en milliers)")
    ax.set_ylabel("milliers d'habitants")
    ax.grid(axis="y", alpha=0.3)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 10, str(v), ha="center", va="bottom", fontsize=10)
    plt.xticks(rotation=20, ha="right")
    fig.tight_layout()
    (HERE / "fixtures").mkdir(exist_ok=True)
    fig.savefig(HERE / "fixtures" / "chart.png")
    mx = max(DATA, key=lambda d: d[1])
    mn = min(DATA, key=lambda d: d[1])
    expected = {"unit": "milliers", "values": dict(DATA),
                "max": {"commune": mx[0], "value": mx[1]},
                "min": {"commune": mn[0], "value": mn[1]}}
    (HERE / "expected.json").write_text(json.dumps(expected, indent=2, ensure_ascii=False),
                                        encoding="utf-8")
    print("chart.png + expected.json ecrits")


if __name__ == "__main__":
    main()
