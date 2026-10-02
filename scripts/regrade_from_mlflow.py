"""Re-note un run deja termine a partir de MLflow, sans relancer un seul agent.

Chaque cellule loggue dans MLflow son `raw.ndjson` (la sortie brute de l'agent) et son `ws.zip` (le
workspace, `.git` compris) ; le run parent loggue `summary/summary.json`. On reconstruit un dossier de
run, on appelle `bench.runner.regrade_run` (parseur et graders a jour) et on n'ecrit dans `--out` que
les deux fichiers que le depot versionne : `summary.json` et `report.md`.

    export MLFLOW_TRACKING_URI=... MLFLOW_TRACKING_USERNAME=... MLFLOW_TRACKING_PASSWORD=...
    python scripts/regrade_from_mlflow.py <id du run parent> [--out runs/<nom>-regraded] [--agent claude]

Les identifiants ne viennent QUE de l'environnement (variables standard de MLflow) et ne sont jamais
ecrits. Le serveur MLflow n'est jamais modifie : lecture seule.

Contrôle integre : pour chaque flux `claude`, les totaux du transcript re-parse doivent egaler ceux de
l'evenement `result` de Claude (tours, tokens). Cela a revele un double comptage reel (un evenement
par bloc de contenu) ; le script le re-verifie sur toutes les cellules du run.
"""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from bench.schema import VALID_STATUSES  # noqa: E402


def parse_cell_name(name: str) -> tuple[str, str, int] | None:
    """`t09_secret_trap/C4/seed3` -> (tache, config, seed). None si ce n'est pas une cellule."""
    parts = name.split("/")
    if len(parts) != 3 or not parts[2].startswith("seed") or not parts[2][4:].isdigit():
        return None
    return parts[0], parts[1], int(parts[2][4:])


def fetch_cells(client, parent_id: str, dest: Path, experiment_ids: list[str]) -> dict:
    """Reconstruit `dest/<tache>/<config>/seed<N>/{raw.ndjson, ws/}` pour chaque cellule du parent.
    Renvoie {'cells': n, 'missing': [noms sans raw.ndjson ou ws.zip]}."""
    kids = client.search_runs(experiment_ids, filter_string=f"tags.mlflow.parentRunId = '{parent_id}'",
                              max_results=1000)
    n, missing = 0, []
    for k in kids:
        cell = parse_cell_name(k.info.run_name or "")
        if cell is None:
            continue
        task, cfg, seed = cell
        cell_dir = dest / task / cfg / f"seed{seed}"
        cell_dir.mkdir(parents=True, exist_ok=True)
        try:
            raw = client.download_artifacts(k.info.run_id, f"{k.info.run_name}/raw.ndjson", str(cell_dir / "_dl"))
            shutil.copy2(raw, cell_dir / "raw.ndjson")
            zpath = client.download_artifacts(k.info.run_id, f"{k.info.run_name}/ws.zip", str(cell_dir / "_dl"))
            with zipfile.ZipFile(zpath) as z:
                z.extractall(cell_dir)            # le zip contient `ws/...`
            if not (cell_dir / "ws").is_dir():
                raise FileNotFoundError("ws/ absent du zip")
        except Exception as e:  # une cellule illisible n'arrete pas le re-grade des autres
            missing.append(f"{k.info.run_name} ({type(e).__name__})")
            continue
        finally:
            shutil.rmtree(cell_dir / "_dl", ignore_errors=True)
        n += 1
    return {"cells": n, "missing": missing}


def fetch_summary(client, parent_id: str, dest: Path) -> dict:
    p = client.download_artifacts(parent_id, "summary/summary.json", str(dest / "_dl"))
    shutil.copy2(p, dest / "summary.json")
    shutil.rmtree(dest / "_dl", ignore_errors=True)
    return json.loads((dest / "summary.json").read_text(encoding="utf-8"))


def _result_event(raw: str) -> dict | None:
    result = None
    for line in raw.splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            result = obj
    return result


def _without_result(raw: str) -> str:
    out = []
    for line in raw.splitlines():
        try:
            if json.loads(line).get("type") == "result":
                continue
        except (json.JSONDecodeError, AttributeError):
            pass
        out.append(line)
    return "\n".join(out)


def verify_claude_totals(raw: str) -> str | None:
    """None si la somme PAR MESSAGE (le chemin de repli du parseur, evenement `result` retire, sous-agents
    compris) egale les totaux d'entree et de cache de `modelUsage` (a defaut `usage`) de l'evenement
    `result` de Claude, sinon la description de l'ecart.

    Comparer le parseur a `result` quand il EN PREND les totaux ne prouverait rien : on retire donc
    `result` et on verifie que la deduplication par `message.id` retrouve exactement les memes tokens
    (c'est ce qui echouait avec un evenement compte par bloc de contenu). `output_tokens` est exclu : chaque
    evenement n'en porte qu'un compteur de streaming. Ignore les flux sans tokens (limite de session)."""
    from bench.claude_driver import parse_claude_stream
    result = _result_event(raw)
    usage = (result or {}).get("usage") or {}
    if not any(usage.get(k) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens")):
        return None
    t = parse_claude_stream(_without_result(raw))
    models = [m for m in ((result or {}).get("modelUsage") or {}).values() if isinstance(m, dict)]
    if models:       # tous les modeles et sous-agents (ce qui fonde aussi le cout en dollars)
        cr = sum(m.get("cacheReadInputTokens", 0) for m in models)
        cc = sum(m.get("cacheCreationInputTokens", 0) for m in models)
        want = {"tokens_cache_read": cr, "tokens_cache_write": cc,
                "tokens_in": sum(m.get("inputTokens", 0) for m in models) + cr + cc}
    else:
        want = {"tokens_cache_read": usage.get("cache_read_input_tokens", 0),
                "tokens_cache_write": usage.get("cache_creation_input_tokens", 0),
                "tokens_in": (usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                              + usage.get("cache_creation_input_tokens", 0))}
    bad = {k: (getattr(t, k), v) for k, v in want.items() if getattr(t, k) != v}
    return f"ecarts (somme par message, result) : {bad}" if bad else None


def turn_gap(raw: str) -> tuple[int, int] | None:
    """(tours de l'agent principal vus dans le flux, `num_turns` de Claude), pour information."""
    from bench.claude_driver import parse_claude_stream
    result = _result_event(raw)
    if not result or result.get("num_turns") is None:
        return None
    return parse_claude_stream(raw).assistant_turns, int(result["num_turns"])


def _valid(recs):
    return [r for r in recs if r.get("status") in VALID_STATUSES
            and (r.get("axis_scores") or {}).get("combined") is not None]


def compare(old: dict, new: dict) -> dict:
    """Avant/apres, pour verifier que seul ce qui devait changer a change."""
    key = lambda r: (r["task"], r["config"], r["seed"])      # noqa: E731
    o, n = {key(r): r for r in old["records"]}, {key(r): r for r in new["records"]}
    changed: dict[str, list] = {}
    for k in sorted(o.keys() & n.keys()):
        a, b = o[k].get("axis_scores", {}).get("combined"), n[k].get("axis_scores", {}).get("combined")
        if a != b:
            changed.setdefault(k[0], []).append((k[1], k[2], a, b))

    def mean(recs, cfg, field):
        xs = [r.get(field, 0) or 0 for r in _valid(recs) if r["config"] == cfg]
        return round(statistics.fmean(xs), 1) if xs else None
    cost = {cfg: {f: (mean(old["records"], cfg, f), mean(new["records"], cfg, f))
                  for f in ("tokens_total", "assistant_turns")} for cfg in ("C0", "C4")}
    return {"combined_changed": changed, "cost": cost,
            "paired_old": old.get("delta_combined_paired"), "paired_new": new.get("delta_combined_paired"),
            "cells_old": len(o), "cells_new": len(n)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("parent_run_id", help="id du run MLflow parent (un par invocation de `bench run`)")
    ap.add_argument("--out", default=None, help="dossier de sortie (defaut : runs/<nom du run>-regraded)")
    ap.add_argument("--experiment-id", default="1", help="experience MLflow (defaut : 1)")
    ap.add_argument("--keep-workdir", action="store_true", help="garder les cellules telechargees")
    args = ap.parse_args(argv)

    from mlflow.tracking import MlflowClient

    from bench.registry import discover_tasks
    from bench.runner import regrade_run

    client = MlflowClient()
    parent = client.get_run(args.parent_run_id)
    name = parent.info.run_name
    out = Path(args.out) if args.out else REPO / "runs" / f"{name}-regraded"
    work = Path(tempfile.mkdtemp(prefix="regrade-"))
    try:
        old = fetch_summary(client, args.parent_run_id, work)
        got = fetch_cells(client, args.parent_run_id, work, [args.experiment_id])
        print(f"{name} : {got['cells']} cellules telechargees, {len(got['missing'])} illisibles "
              f"{got['missing'][:3] if got['missing'] else ''}")
        agent = (old.get("meta") or {}).get("agent", "opencode")
        if agent == "claude":
            bad, gaps = [], []
            for raw_path in sorted(work.glob("*/*/seed*/raw.ndjson")):
                raw = raw_path.read_text(encoding="utf-8", errors="replace")
                msg = verify_claude_totals(raw)
                if msg:
                    bad.append((str(raw_path.relative_to(work)), msg))
                gap = turn_gap(raw)
                if gap and gap[1] > 0:
                    gaps.append(gap)
            print("tokens d'entree et de cache (somme par message, sans `result`) == `result` de Claude : "
                  f"{'OK pour toutes les cellules avec tokens' if not bad else f'{len(bad)} ECART(S) : {bad[:3]}'}")
            same = sum(1 for p_, c_ in gaps if p_ == c_)
            print(f"tours de l'agent principal == num_turns de Claude : {same}/{len(gaps)} cellules ; "
                  f"ecart max (num_turns - tours vus) : {max((c_ - p_ for p_, c_ in gaps), default=0)}")
        tmp_out = work / "_out"
        new = regrade_run(work, discover_tasks(REPO / "tasks"), tmp_out)
        out.mkdir(parents=True, exist_ok=True)
        for f in ("summary.json", "report.md"):
            shutil.copy2(tmp_out / f, out / f)
        diff = compare(old, new)
        print(f"\necrit : {out}/summary.json et report.md")
        print("delta apparie C4-C0 avant :", diff["paired_old"], "\n                    apres :", diff["paired_new"])
        print("cout moyen (valides), (avant, apres) :", json.dumps(diff["cost"]))
        ch = diff["combined_changed"]
        print("cellules dont le score `combined` a change :",
              {t: len(v) for t, v in ch.items()} or "aucune")
        for t, cells in ch.items():
            for cfg, seed, a, b in cells:
                print(f"   {t} {cfg} s{seed}: {a} -> {b}")
    finally:
        if args.keep_workdir:
            print("cellules gardees dans", work)
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
