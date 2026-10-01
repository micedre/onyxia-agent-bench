"""Comparaison de plusieurs runs (agent x modele) : `bench compare runs/A runs/B ...`.

Chaque run est un `summary.json` produit par `bench run` (un agent, un modele, des configs).
On y lit, par bras (run, config) : le score combine moyen (IC95 bootstrap sur les cellules), le
delta apparie contexte (config haute - config basse) par run, et le cout. Entre runs, la
difference de deux bras est calculee sur les moyennes PAR TACHE (et non par (tache, seed) : le
seed n'est qu'une etiquette de cellule, jamais transmis a l'agent, donc deux runs d'agents
differents ne sont pas apparies au niveau du seed) ; l'IC est un bootstrap sur les taches.

Qualite et cout restent cote a cote, jamais combines (cf. schema.QUALITY_AXES). Le cout en
dollars n'est renseigne que quand l'agent le publie (Claude) ; les modeles auto-heberges sont a 0.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

from bench.report import _fmt, _fmt_int, _table
from bench.runner import _bootstrap_ci
from bench.schema import VALID_STATUSES


def load_run(run_dir: Path) -> dict:
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    meta = summary.get("meta") or {}
    label = f"{meta.get('agent', 'opencode')}:{meta.get('model', run_dir.name)}"
    recs = [r for r in summary["records"]
            if r.get("status", "ok" if not r.get("error") else "error") in VALID_STATUSES
            and (r.get("axis_scores") or {}).get("combined") is not None]
    return {"dir": run_dir, "label": label, "meta": meta, "records": recs,
            "all_records": summary["records"]}


def _ci_txt(ci: dict | None, signed: bool = False) -> str:
    if not ci:
        return "–"
    f = (lambda x: f"{x:+.2f}") if signed else (lambda x: f"{x:.2f}")
    return f"{f(ci['mean'])} [{f(ci['ci95'][0])}, {f(ci['ci95'][1])}] (n={ci['n']})"


def _combined(recs: list[dict], config: str) -> dict[tuple, float]:
    return {(r["task"], r["seed"]): r["axis_scores"]["combined"] for r in recs if r["config"] == config}


def _task_means(recs: list[dict], config: str) -> dict[str, float]:
    by: dict[str, list[float]] = {}
    for r in recs:
        if r["config"] == config:
            by.setdefault(r["task"], []).append(r["axis_scores"]["combined"])
    return {t: statistics.fmean(v) for t, v in by.items()}


def arm_table(runs: list[dict], low: str, high: str) -> str:
    rows = []
    for run in runs:
        lo, hi = _combined(run["records"], low), _combined(run["records"], high)
        diffs = [hi[k] - lo[k] for k in lo.keys() & hi.keys()]
        rows.append([run["label"],
                     _ci_txt(_bootstrap_ci(list(lo.values()))),
                     _ci_txt(_bootstrap_ci(list(hi.values()))),
                     _ci_txt(_bootstrap_ci(diffs), signed=True)])
    return _table(["Run (agent:modele)", f"{low} (nu)", f"{high} (complet)",
                   f"Delta apparie {high}-{low}"], rows)


def per_task_table(runs: list[dict], low: str, high: str) -> str:
    tasks = sorted({r["task"] for run in runs for r in run["records"]})
    headers = ["Tache"]
    for run in runs:
        headers += [f"{run['label']} {low}", f"{run['label']} {high}"]
    rows = []
    for t in tasks:
        row = [t]
        for run in runs:
            for cfg in (low, high):
                v = _task_means(run["records"], cfg).get(t)
                row.append(_fmt(v))
        rows.append(row)
    return _table(headers, rows)


def cross_table(runs: list[dict], low: str, high: str) -> str:
    """Difference de bras entre runs (premier run = reference), bootstrap sur les taches."""
    if len(runs) < 2:
        return ""
    ref = runs[0]
    rows = []
    for other in runs[1:]:
        for cfg_o, cfg_r in ((low, low), (high, high), (low, high)):
            a, b = _task_means(other["records"], cfg_o), _task_means(ref["records"], cfg_r)
            common = sorted(a.keys() & b.keys())
            diffs = [a[t] - b[t] for t in common]
            rows.append([f"{other['label']} {cfg_o}", f"{ref['label']} {cfg_r}",
                         _ci_txt(_bootstrap_ci(diffs), signed=True)])
    return _table(["Bras", "moins bras de reference", "Difference (par tache)"], rows)


def cost_table(runs: list[dict], low: str, high: str) -> str:
    rows = []
    for run in runs:
        for cfg in (low, high):
            cells = [r for r in run["records"] if r["config"] == cfg]
            if not cells:
                continue

            def mean(key, _c=cells):
                return statistics.fmean((r.get(key) or 0) for r in _c)
            non_cached = statistics.fmean(
                (r.get("tokens_total") or 0) - (r.get("tokens_cache_read") or 0) for r in cells)
            usd = mean("cost")
            rows.append([run["label"], cfg, str(len(cells)), _fmt_int(mean("tokens_total")),
                         _fmt_int(non_cached), f"{mean('assistant_turns'):.1f}",
                         f"{mean('agent_s'):.0f}", f"{usd:.3f}" if usd else "–",
                         f"{mean('permission_rejections'):.1f}"])
    return _table(["Run", "Config", "Cellules", "Tokens (total)", "Tokens hors cache lu",
                   "Tours", "Temps agent (s)", "USD / cellule", "Rejets permission"], rows)


def _disambiguate(runs: list[dict]) -> None:
    """Deux runs du meme agent:modele (ex. deux jeux de seeds) recoivent leur nom de dossier."""
    labels = [r["label"] for r in runs]
    for r in runs:
        if labels.count(r["label"]) > 1:
            r["label"] = f"{r['label']} ({r['dir'].name})"


def render(runs: list[dict], low: str = "C0", high: str = "C4") -> str:
    _disambiguate(runs)
    parts = ["# Comparaison de runs\n",
             "Runs : " + ", ".join(f"`{r['dir'].name}` ({r['label']}, "
                                   f"{len(r['records'])}/{len(r['all_records'])} cellules valides)"
                                   for r in runs) + "\n",
             "## Qualite (score combine) et apport du contexte\n", arm_table(runs, low, high),
             "\nLe delta apparie est calcule dans chaque run, sur les paires (tache, seed). "
             "Comparer deux agents differents compare aussi deux harnais (prompt systeme, "
             "outils, compaction) : lire les ecarts entre runs comme descriptifs.\n",
             "## Par tache (moyenne des seeds)\n", per_task_table(runs, low, high)]
    cross = cross_table(runs, low, high)
    if cross:
        parts += ["\n## Ecarts entre runs (reference = premier run)\n", cross,
                  "\nBootstrap sur les taches : avec peu de taches l'IC est large, et c'est voulu."]
    parts += ["\n## Cout\n", cost_table(runs, low, high),
              "\nLes tokens comptent le cache lu a chaque tour (cf. `schema.Transcript`) ; "
              "la colonne hors cache lu est la plus comparable entre agents. L'USD n'existe que "
              "pour les agents qui le publient."]
    return "\n".join(parts) + "\n"
