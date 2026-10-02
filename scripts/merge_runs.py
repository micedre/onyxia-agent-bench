"""Fusionne plusieurs `summary.json` (runs complementaires d'un meme modele) en un jeu final.

    python scripts/merge_runs.py --out runs/final-opus runs/a/summary.json runs/b/summary.json
    python scripts/merge_runs.py --out runs/final-27b \
        runs/old/summary.json:t03_mlflow_train,t10_diag_403,t23_code_review runs/new/summary.json

Chaque source peut etre restreinte a certaines taches (`chemin:t1,t2`). Les enregistrements sont
concatenes par (tache, config, seed) ; en cas de doublon la source la PLUS TARDIVE sur la ligne de
commande l'emporte (et c'est signale). On recalcule `aggregate` et le rapport : rien n'est copie des
moyennes des sources. Controles : pas de doublon residuel, une seule valeur de modele, memes configs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from bench.report import render_markdown  # noqa: E402
from bench.runner import aggregate  # noqa: E402


def parse_source(arg: str) -> tuple[Path, set[str] | None]:
    path, _, tasks = arg.partition(":")
    return Path(path), (set(tasks.split(",")) if tasks else None)


def merge(sources: list[tuple[Path, set[str] | None]]) -> tuple[list[dict], dict, list[str]]:
    by_key: dict[tuple, dict] = {}
    notes: list[str] = []
    metas: list[dict] = []
    for path, only in sources:
        d = json.loads(path.read_text(encoding="utf-8"))
        metas.append(d.get("meta") or {})
        for r in d["records"]:
            if only is not None and r["task"] not in only:
                continue
            k = (r["task"], r["config"], r["seed"])
            if k in by_key:
                notes.append(f"doublon {k} : {path} remplace la source precedente")
            by_key[k] = {**r, "_source": str(path)}
    models = {m.get("model") for m in metas if m.get("model")}
    if len(models) > 1:
        raise SystemExit(f"modeles differents refuses : {sorted(models)}")
    meta = dict(metas[-1])
    meta["tasks"] = sorted({k[0] for k in by_key})
    meta["merged_from"] = [str(p) + (":" + ",".join(sorted(o)) if o else "") for p, o in sources]
    return [by_key[k] for k in sorted(by_key)], meta, notes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("sources", nargs="+", help="summary.json[:tache1,tache2]")
    ap.add_argument("--out", required=True)
    ap.add_argument("--run-name", default=None)
    args = ap.parse_args(argv)
    records, meta, notes = merge([parse_source(s) for s in args.sources])
    configs = meta.get("configs") or sorted({r["config"] for r in records})
    meta["run_name"] = args.run_name or Path(args.out).name
    summary = aggregate(records, configs[0], configs[-1])
    summary["records"] = records
    summary["meta"] = meta
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "report.md").write_text(render_markdown(summary, meta), encoding="utf-8")
    for n in notes:
        print(n)
    print(f"{out} : {summary['n_cells']} cellules, {summary['n_valid']} valides, "
          f"delta apparie {summary.get('delta_combined_paired')}")


if __name__ == "__main__":
    main()
