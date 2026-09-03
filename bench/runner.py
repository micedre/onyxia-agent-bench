"""Orchestrateur : pour chaque (task x config x seed), isole, execute, note.

Les cellules sont independantes (repertoire/pod dedie chacune) et executees en parallele
via un ThreadPoolExecutor (`workers`) - adapte ici puisque le cout dominant par cellule est
l'appel agent (subprocess `opencode`, ou cycle de vie complet d'un Job k8s), domine par de
l'attente I/O qui libere le GIL. Seule la finalisation d'une cellule (bookkeeping +
`logger.log_cell` + affichage) est serialisee derriere un verrou.

L'ordre des cellules est melange (graine fixe) : sans ca, elles tournent dans l'ordre
task x config x seed et une saturation du cluster en fin de run efface entierement la
derniere tache (constate : t10 = 15/15 cellules "pod jamais pret" sur un run reel)."""
from __future__ import annotations

import json
import random
import shutil
import statistics
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from bench.configs import materialize
from bench.opencode_driver import BaseDriver, parse_output
from bench.report import render_markdown
from bench.schema import (
    AXES,
    VALID_STATUSES,
    Check,
    ConfigSpec,
    GradeReport,
    RunResult,
    TaskSpec,
    cell_status,
    combined_score,
)

# Budget de tokens par defaut pour l'axe efficiency (somme input+output+raisonnement sur
# tous les tours). Ordre de grandeur observe : ~100k tokens par cellule pour un agent nu
# qui reussit une tache simple, ~500k pour une config complete qui epuise son temps.
DEFAULT_BUDGET_TOKENS = 500_000
SHUFFLE_SEED = 20260902


@dataclass
class GradeContext:
    """Ce que recoit un grade(ctx) de task."""
    workspace: Path
    transcript: object
    files_changed: list[str]
    run: RunResult
    metrics: dict = field(default_factory=dict)
    task: TaskSpec | None = None


def _git(ws: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(ws), *args], capture_output=True, text=True)
    return r.stdout


def _init_workspace(ws: Path, task: TaskSpec):
    ws.mkdir(parents=True, exist_ok=True)
    fixtures = task.dir / "fixtures"
    if fixtures.is_dir():
        for p in fixtures.rglob("*"):
            if p.is_file():
                dst = ws / p.relative_to(fixtures)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
    _git(ws, "init", "-q")
    _git(ws, "add", "-A")
    _git(ws, "-c", "user.email=bench@local", "-c", "user.name=bench",
         "commit", "-q", "-m", "fixtures", "--allow-empty")
    # fixtures_untracked/ : copiees APRES le commit initial, donc presentes sur disque mais
    # non suivies au depart (ex. un secret plante pour un piege de securite - voir T09).
    # Un fichier `X.untracked` y est depose sous le nom `X` : ca permet de versionner dans ce
    # depot un fichier que notre propre .gitignore bloquerait (`.env`), sans quoi le piege
    # n'existe tout simplement pas dans le workspace (constate : t09 passait 15/15 a vide).
    untracked = task.dir / "fixtures_untracked"
    if untracked.is_dir():
        for p in untracked.rglob("*"):
            if p.is_file():
                rel = p.relative_to(untracked)
                if rel.name.endswith(".untracked"):
                    rel = rel.with_name(rel.name[: -len(".untracked")])
                dst = ws / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)


def _prune_noise(ws: Path):
    """Supprime les caches d'installation (ex. `.opencode/node_modules`, installes par
    opencode lui-meme pour les providers npm) avant capture/artefacts : ce ne sont pas des
    sorties de l'agent et leur volume (des milliers de fichiers) rend l'upload MLflow
    impraticable."""
    for nm in ws.rglob("node_modules"):
        if nm.is_dir():
            shutil.rmtree(nm, ignore_errors=True)


def _changed_files(ws: Path) -> list[str]:
    out = _git(ws, "status", "--porcelain", "--untracked-files=all")
    files = []
    for line in out.splitlines():
        path = line[3:].strip()
        if path and not path.endswith("/"):
            files.append(path)
    return files


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def efficiency_checks(task: TaskSpec, run: RunResult) -> list[Check]:
    """Axe efficiency, uniforme pour toutes les taches : cout en tokens et en temps par
    rapport au budget de la tache (task.yaml : budget_tokens / budget_s, sinon defauts).
    Score lineaire : 1.0 a cout nul, 0.0 au budget (donc un timeout vaut 0 en temps)."""
    t = run.transcript
    if run.status not in VALID_STATUSES or (t.tokens_total == 0 and not t.events):
        return []
    checks = []
    budget_tok = task.budget_tokens or DEFAULT_BUDGET_TOKENS
    if t.tokens_total > 0:
        s = _clamp(1.0 - t.tokens_total / budget_tok)
        checks.append(Check("token_budget", s >= 0.5, s, axis="efficiency",
                            detail=f"{t.tokens_total} tokens / budget {budget_tok}"))
    # jamais au-dela du timeout : sinon une cellule qui epuise son budget de temps garderait
    # un score de temps positif
    budget_s = min(task.budget_s or task.timeout_s, task.timeout_s)
    used_s = run.agent_s or run.wall_clock_s
    s = _clamp(1.0 - used_s / budget_s)
    checks.append(Check("time_budget", s >= 0.5, s, axis="efficiency",
                        detail=f"{used_s:.0f}s / budget {budget_s}s"))
    return checks


def _transcript_metrics(run: RunResult) -> dict:
    t = run.transcript
    return {
        "assistant_turns": t.assistant_turns,
        "permission_rejections": t.permission_rejections,
        "tool_errors": t.tool_errors,
        "subagent_calls": t.subagent_calls,
        "context_tokens_first": t.context_tokens_first,
        "context_tokens_last": t.context_tokens_last,
        "tokens_reasoning": t.tokens_reasoning,
    }


def grade_run(task: TaskSpec, run: RunResult) -> tuple[GradeReport, GradeContext]:
    """Note un RunResult (fichiers deja rapatries dans run.workspace)."""
    run.status = cell_status(run)
    ctx = GradeContext(run.workspace, run.transcript, run.files_changed, run, task=task)
    try:
        checks: list[Check] = task.grade_fn(ctx)
    except Exception as e:  # une task ne doit pas casser le benchmark
        checks = [Check("grader_error", False, 0.0, detail=repr(e))]
    checks += efficiency_checks(task, run)
    ctx.metrics.update(_transcript_metrics(run))
    return GradeReport(checks), ctx


def _write_cell_artifacts(cell_dir: Path, run: RunResult, report: GradeReport,
                          ctx: GradeContext):
    cell_dir.mkdir(parents=True, exist_ok=True)
    t = run.transcript
    (cell_dir / "transcript.json").write_text(json.dumps(
        {"text": t.text,
         "events": [e.__dict__ for e in t.events],
         "tokens_in": t.tokens_in, "tokens_out": t.tokens_out,
         "tokens_reasoning": t.tokens_reasoning, "cost": t.cost,
         "assistant_turns": t.assistant_turns,
         "context_tokens_first": t.context_tokens_first,
         "context_tokens_last": t.context_tokens_last,
         "permission_rejections": t.permission_rejections, "tool_errors": t.tool_errors,
         "subagent_calls": t.subagent_calls},
        indent=2, ensure_ascii=False), encoding="utf-8")
    # Sortie brute d'opencode (nd-JSON + [STDERR]/[TIMEOUT]) : la seule source pour
    # re-noter/re-parser une cellule apres coup (voir `bench regrade`).
    if t.raw_stdout:
        (cell_dir / "raw.ndjson").write_text(t.raw_stdout, encoding="utf-8")
    (cell_dir / "grade_report.json").write_text(json.dumps(
        {**report.to_dict(), "metrics": ctx.metrics, "files_changed": run.files_changed,
         "error": run.error, "status": run.status, "exit_code": run.exit_code,
         "timed_out": run.timed_out, "wall_clock_s": round(run.wall_clock_s, 3),
         "agent_s": round(run.agent_s, 3)},
        indent=2, ensure_ascii=False), encoding="utf-8")


def run_cell(task: TaskSpec, config: ConfigSpec, base: str, configs_dir: Path,
             model: str, seed: int, driver: BaseDriver, cell_dir: Path) -> tuple:
    ws = cell_dir / "ws"
    _init_workspace(ws, task)
    materialize(config, configs_dir, base, ws, model=task.model or model)

    run: RunResult = driver.run(task, ws, task.model or model, seed, config.id)
    _prune_noise(ws)
    run.files_changed = _changed_files(ws)
    report, ctx = grade_run(task, run)
    _write_cell_artifacts(cell_dir, run, report, ctx)
    return run, report, ctx


# --------------------------------------------------------------------------------------
# Agregation
# --------------------------------------------------------------------------------------
def _bootstrap_ci(values: list[float], n_boot: int = 2000, seed: int = 0) -> dict | None:
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return None
    rng = random.Random(seed)
    n = len(vals)
    means = sorted(statistics.fmean(rng.choices(vals, k=n)) for _ in range(n_boot))
    return {"mean": round(statistics.fmean(vals), 4),
            "ci95": [round(means[int(0.025 * n_boot)], 4), round(means[int(0.975 * n_boot)], 4)],
            "n": n}


def aggregate(records: list[dict], low: str = "C0", high: str = "C4") -> dict:
    """Moyennes par (config, axe) sur les cellules VALIDES (status ok/timeout), bloc de
    fiabilite par config, intervalles de confiance bootstrap, delta high-low.

    `combined` d'une config = moyenne non ponderee de ses moyennes d'axes (pas un 6e axe :
    l'ancienne version re-moyennait aussi le `combined` par cellule, ce qui decalait chaque
    score de config d'environ 0.02 a 0.03)."""
    configs = []
    for r in records:
        if r["config"] not in configs:
            configs.append(r["config"])

    valid = [r for r in records
             if r.get("status", "ok" if not r.get("error") else "error") in VALID_STATUSES
             and r.get("axis_scores")]
    by_config: dict[str, dict[str, list[float]]] = {c: {} for c in configs}
    for r in valid:
        for axis, val in r["axis_scores"].items():
            if axis in AXES and val is not None:
                by_config[r["config"]].setdefault(axis, []).append(val)

    mean_by_config = {
        cfg: {axis: round(statistics.fmean(vals), 4) for axis, vals in axes.items()}
        for cfg, axes in by_config.items()
    }
    for axes in mean_by_config.values():
        axes["combined"] = combined_score(axes)

    # Fiabilite + cout par config (toutes cellules, y compris non valides)
    reliability = {}
    for cfg in configs:
        cells = [r for r in records if r["config"] == cfg]
        counts = {}
        for r in cells:
            st = r.get("status", "ok" if not r.get("error") else "error")
            counts[st] = counts.get(st, 0) + 1
        ran = counts.get("ok", 0) + counts.get("timeout", 0)
        vcells = [r for r in valid if r["config"] == cfg]

        def _mean(key, _cells=vcells):
            xs = [r.get(key, 0) or 0 for r in _cells]
            return round(statistics.fmean(xs), 1) if xs else None
        reliability[cfg] = {
            "n_cells": len(cells), "n_valid": len(vcells), "status_counts": counts,
            "timeout_rate": round(counts.get("timeout", 0) / ran, 3) if ran else None,
            "mean_tokens_total": _mean("tokens_total"),
            "mean_wall_clock_s": _mean("wall_clock_s"),
            "mean_agent_s": _mean("agent_s"),
            "mean_tool_calls": _mean("tool_calls"),
            "mean_assistant_turns": _mean("assistant_turns"),
            "mean_permission_rejections": _mean("permission_rejections"),
            "mean_subagent_calls": _mean("subagent_calls"),
        }

    # Couverture : combien de taches alimentent chaque axe. Un axe nourri par 2 taches (repro)
    # bouge beaucoup plus au bruit qu'un axe nourri par 11 (functional) - le rapport l'affiche
    # pour que personne ne lise un ecart d'axe faiblement couvert comme un resultat.
    axis_coverage = {}
    for axis in AXES:
        tasks_for_axis = sorted({r["task"] for r in valid
                                 if r["axis_scores"].get(axis) is not None})
        n_cells_axis = sum(1 for r in valid if r["axis_scores"].get(axis) is not None)
        if tasks_for_axis:
            axis_coverage[axis] = {"n_tasks": len(tasks_for_axis), "n_cells": n_cells_axis,
                                   "tasks": tasks_for_axis}

    # IC bootstrap sur la moyenne des `combined` par cellule
    ci_by_config = {}
    for cfg in configs:
        vals = [r["axis_scores"].get("combined") for r in valid if r["config"] == cfg]
        ci = _bootstrap_ci(vals)
        if ci:
            ci_by_config[cfg] = ci

    delta = {}
    delta_paired = None
    if low in mean_by_config and high in mean_by_config:
        for axis in AXES + ["combined"]:
            lo = mean_by_config[low].get(axis)
            hi = mean_by_config[high].get(axis)
            if lo is not None and hi is not None:
                delta[axis] = round(hi - lo, 4)
        # delta apparie par (task, seed) : ne compare que les paires ou les deux cellules
        # sont valides, pour que les echecs d'infra n'entrent pas dans le delta.
        lo_cells = {(r["task"], r["seed"]): r["axis_scores"].get("combined")
                    for r in valid if r["config"] == low}
        hi_cells = {(r["task"], r["seed"]): r["axis_scores"].get("combined")
                    for r in valid if r["config"] == high}
        diffs = [hi_cells[k] - lo_cells[k] for k in lo_cells.keys() & hi_cells.keys()
                 if lo_cells[k] is not None and hi_cells[k] is not None]
        delta_paired = _bootstrap_ci(diffs)

    return {
        "mean_by_config": mean_by_config,
        "axis_coverage": axis_coverage,
        "delta_by_axis": delta,
        "delta_combined_paired": delta_paired,
        "ci_by_config": ci_by_config,
        "reliability": reliability,
        "n_cells": len(records),
        "n_valid": len(valid),
        "compared": {"low": low, "high": high},
    }


def _build_rec(task_id: str, config_id: str, seed: int, run: RunResult,
              report: GradeReport, metrics: dict) -> dict:
    report_dict = report.to_dict()
    axis_scores = report_dict["axis_scores"]
    axis_scores["combined"] = combined_score(axis_scores)
    t = run.transcript
    return {
        "task": task_id, "config": config_id, "seed": seed,
        "status": run.status,
        "axis_scores": axis_scores,
        "checks": report_dict["checks"],
        "safety_violations": report.safety_violations,
        "tokens_total": t.tokens_total,
        "tokens_in": t.tokens_in, "tokens_out": t.tokens_out,
        "tokens_reasoning": t.tokens_reasoning,
        "context_tokens_first": t.context_tokens_first,
        "context_tokens_last": t.context_tokens_last,
        "cost": round(t.cost, 6),
        "tool_calls": len(t.tool_events),
        "assistant_turns": t.assistant_turns,
        "steps": t.assistant_turns or len(t.events),
        "permission_rejections": t.permission_rejections,
        "tool_errors": t.tool_errors,
        "subagent_calls": t.subagent_calls,
        "wall_clock_s": round(run.wall_clock_s, 3),
        "agent_s": round(run.agent_s, 3),
        "exit_code": run.exit_code,
        "timed_out": run.timed_out,
        "metrics": metrics,
        "error": run.error,
    }


def _crashed_rec(task_id: str, config_id: str, seed: int, err: str) -> dict:
    return {
        "task": task_id, "config": config_id, "seed": seed, "status": "error",
        "axis_scores": {}, "checks": [], "safety_violations": 0,
        "tokens_total": 0, "tool_calls": 0, "steps": 0, "wall_clock_s": 0.0, "agent_s": 0.0,
        "assistant_turns": 0, "permission_rejections": 0, "subagent_calls": 0,
        "metrics": {}, "error": f"cell_crashed: {err}",
    }


def _finalize(records: list[dict], out_dir: Path, meta: dict, low: str, high: str) -> dict:
    summary = aggregate(records, low=low, high=high)
    summary["meta"] = meta
    summary["records"] = records
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_md = render_markdown(summary, meta)
    (out_dir / "report.md").write_text(report_md, encoding="utf-8")
    return summary, report_md


def run_benchmark(tasks: list[TaskSpec], configs: list[ConfigSpec], base: str,
                  configs_dir: Path, model: str, seeds: int, driver: BaseDriver,
                  out_dir: Path, logger, workers: int = 1, meta: dict | None = None) -> dict:
    records: list[dict] = []
    lock = threading.Lock()
    cells = [(task, config, seed, out_dir / task.id / config.id / f"seed{seed}")
             for task in tasks for config in configs for seed in range(seeds)]
    random.Random(SHUFFLE_SEED).shuffle(cells)

    def run_and_finalize(task: TaskSpec, config: ConfigSpec, seed: int, cell_dir: Path):
        try:
            run, report, ctx = run_cell(task, config, base, configs_dir,
                                        model, seed, driver, cell_dir)
            rec = _build_rec(task.id, config.id, seed, run, report, ctx.metrics)
        except Exception as e:  # une cellule ne doit jamais casser tout le run
            run = None
            rec = _crashed_rec(task.id, config.id, seed, repr(e))
        # Seule etape serialisee : le reste (isolation, execution agent, notation) tourne
        # deja en parallele au-dessus, sans etat partage.
        with lock:
            records.append(rec)
            if run is not None:
                logger.log_cell(run, report, ctx.metrics, cell_dir)
            _print_cell(rec)

    meta = dict(meta or {})
    meta.update({"run_name": out_dir.name, "model": model, "seeds": seeds,
                 "tasks": [t.id for t in tasks], "configs": [c.id for c in configs]})
    with logger:
        if hasattr(logger, "log_meta"):
            logger.log_meta(meta)
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = [pool.submit(run_and_finalize, *cell) for cell in cells]
            for future in as_completed(futures):
                future.result()  # relance ici toute exception non-cellule (bug reel)
        # ordre stable dans summary/report quel que soit l'ordre d'execution
        records.sort(key=lambda r: (r["task"], r["config"], r["seed"]))
        summary, report_md = _finalize(records, out_dir, meta, configs[0].id, configs[-1].id)
        logger.log_summary(summary, report_md)
    return summary


# --------------------------------------------------------------------------------------
# Re-notation d'un run existant (graders/parseur modifies) sans relancer les agents
# --------------------------------------------------------------------------------------
def regrade_run(run_dir: Path, tasks: dict[str, TaskSpec], out_dir: Path | None = None) -> dict:
    """Recharge chaque cellule (`ws/` + `raw.ndjson` ou l'ancien `transcript.json`), re-parse
    le transcript, re-execute grade(ctx) et reconstruit summary/report dans `out_dir`
    (defaut : `<run_dir>/regrade/`). Les cellules dont la tache n'existe plus sont ignorees."""
    old = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    old_recs = {(r["task"], r["config"], r["seed"]): r for r in old.get("records", [])}
    meta = old.get("meta") or {"run_name": run_dir.name, "model": "?", "seeds": "?",
                               "tasks": sorted({k[0] for k in old_recs}),
                               "configs": sorted({k[1] for k in old_recs})}
    out_dir = out_dir or (run_dir / "regrade")
    out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for (task_id, config_id, seed), orec in sorted(old_recs.items()):
        task = tasks.get(task_id)
        cell_dir = run_dir / task_id / config_id / f"seed{seed}"
        ws = cell_dir / "ws"
        if task is None or not ws.is_dir():
            continue
        raw_path = cell_dir / "raw.ndjson"
        if raw_path.is_file():
            raw = raw_path.read_text(encoding="utf-8", errors="replace")
        else:
            tj = json.loads((cell_dir / "transcript.json").read_text(encoding="utf-8"))
            raw = tj.get("text", "")  # anciens runs : le texte etait le stdout brut
        transcript = parse_output(raw)
        run = RunResult(task_id, config_id, meta.get("model", "?"), seed, ws, transcript,
                        exit_code=orec.get("exit_code", 1 if orec.get("error") else 0),
                        timed_out=bool(orec.get("timed_out")) or "exit=124" in (orec.get("error") or ""),
                        wall_clock_s=orec.get("wall_clock_s", 0.0), error=orec.get("error"))
        run.agent_s = orec.get("agent_s", 0.0)
        run.files_changed = _changed_files(ws)
        report, ctx = grade_run(task, run)
        (out_dir / task_id / config_id / f"seed{seed}").mkdir(parents=True, exist_ok=True)
        (out_dir / task_id / config_id / f"seed{seed}" / "grade_report.json").write_text(
            json.dumps({**report.to_dict(), "metrics": ctx.metrics, "status": run.status,
                        "error": run.error}, indent=2, ensure_ascii=False), encoding="utf-8")
        records.append(_build_rec(task_id, config_id, seed, run, report, ctx.metrics))
    configs = meta.get("configs") or sorted({r["config"] for r in records})
    summary, _ = _finalize(records, out_dir, meta, configs[0], configs[-1])
    return summary


def _print_cell(rec: dict):
    scores = " ".join(f"{a[:4]}={rec['axis_scores'][a]:.2f}"
                      for a in AXES + ["combined"] if rec["axis_scores"].get(a) is not None)
    extra = f" steps2diag={rec['metrics'].get('steps_to_diagnosis')}" \
        if "steps_to_diagnosis" in rec["metrics"] else ""
    status = "" if rec.get("status", "ok") == "ok" else f" [{rec['status']}]"
    print(f"  [{rec['task']:16s} {rec['config']:3s} s{rec['seed']}]{status} {scores} "
          f"tok={rec['tokens_total']} turns={rec.get('assistant_turns', 0)} "
          f"tools={rec['tool_calls']} rej={rec.get('permission_rejections', 0)}{extra}")
