"""Orchestrateur sequentiel : pour chaque (task x config x seed), isole, execute, note."""
from __future__ import annotations

import json
import shutil
import statistics
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from bench.configs import materialize
from bench.opencode_driver import BaseDriver
from bench.report import render_markdown
from bench.schema import AXES, Check, ConfigSpec, GradeReport, RunResult, TaskSpec, combined_score


@dataclass
class GradeContext:
    """Ce que recoit un grade(ctx) de task."""
    workspace: Path
    transcript: object
    files_changed: list[str]
    run: RunResult
    metrics: dict = field(default_factory=dict)


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
    untracked = task.dir / "fixtures_untracked"
    if untracked.is_dir():
        for p in untracked.rglob("*"):
            if p.is_file():
                dst = ws / p.relative_to(untracked)
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
    out = _git(ws, "status", "--porcelain")
    files = []
    for line in out.splitlines():
        path = line[3:].strip()
        if path and not path.endswith("/"):
            files.append(path)
    return files


def run_cell(task: TaskSpec, config: ConfigSpec, base: str, configs_dir: Path,
             model: str, seed: int, driver: BaseDriver, cell_dir: Path) -> tuple:
    ws = cell_dir / "ws"
    _init_workspace(ws, task)
    materialize(config, configs_dir, base, ws)

    run: RunResult = driver.run(task, ws, model, seed, config.id)
    _prune_noise(ws)
    run.files_changed = _changed_files(ws)

    ctx = GradeContext(ws, run.transcript, run.files_changed, run)
    try:
        checks: list[Check] = task.grade_fn(ctx)
    except Exception as e:  # une task ne doit pas casser le benchmark
        checks = [Check("grader_error", False, 0.0, detail=repr(e))]
    report = GradeReport(checks)

    # artefacts audit
    cell_dir.mkdir(parents=True, exist_ok=True)
    (cell_dir / "transcript.json").write_text(json.dumps(
        {"text": run.transcript.text,
         "events": [e.__dict__ for e in run.transcript.events],
         "tokens_in": run.transcript.tokens_in, "tokens_out": run.transcript.tokens_out},
        indent=2, ensure_ascii=False), encoding="utf-8")
    (cell_dir / "grade_report.json").write_text(json.dumps(
        {**report.to_dict(), "metrics": ctx.metrics, "files_changed": run.files_changed,
         "error": run.error}, indent=2, ensure_ascii=False), encoding="utf-8")
    return run, report, ctx


def aggregate(records: list[dict], low: str = "C0", high: str = "C4") -> dict:
    """Moyenne par (config, axe) sur les seeds/tasks + delta high-low."""
    by_config: dict[str, dict[str, list[float]]] = {}
    for r in records:
        cfg = r["config"]
        for axis, val in r["axis_scores"].items():
            if val is not None:
                by_config.setdefault(cfg, {}).setdefault(axis, []).append(val)

    mean_by_config = {
        cfg: {axis: round(statistics.fmean(vals), 4) for axis, vals in axes.items()}
        for cfg, axes in by_config.items()
    }
    for axes in mean_by_config.values():
        axes["combined"] = combined_score(axes)
    delta = {}
    if low in mean_by_config and high in mean_by_config:
        for axis in AXES + ["combined"]:
            lo = mean_by_config[low].get(axis)
            hi = mean_by_config[high].get(axis)
            if lo is not None and hi is not None:
                delta[axis] = round(hi - lo, 4)
    return {
        "mean_by_config": mean_by_config,
        "delta_by_axis": delta,
        "n_cells": len(records),
        "compared": {"low": low, "high": high},
    }


def run_benchmark(tasks: list[TaskSpec], configs: list[ConfigSpec], base: str,
                  configs_dir: Path, model: str, seeds: int, driver: BaseDriver,
                  out_dir: Path, logger) -> dict:
    records: list[dict] = []
    with logger:
        for task in tasks:
            for config in configs:
                for seed in range(seeds):
                    cell_dir = out_dir / task.id / config.id / f"seed{seed}"
                    run, report, ctx = run_cell(task, config, base, configs_dir,
                                                 model, seed, driver, cell_dir)
                    report_dict = report.to_dict()
                    axis_scores = report_dict["axis_scores"]
                    axis_scores["combined"] = combined_score(axis_scores)
                    rec = {
                        "task": task.id, "config": config.id, "seed": seed,
                        "axis_scores": axis_scores,
                        "checks": report_dict["checks"],
                        "safety_violations": report.safety_violations,
                        "tokens_total": run.transcript.tokens_total,
                        "tool_calls": len(run.transcript.tool_events),
                        "steps": len(run.transcript.events),
                        "wall_clock_s": round(run.wall_clock_s, 3),
                        "metrics": ctx.metrics,
                        "error": run.error,
                    }
                    records.append(rec)
                    logger.log_cell(run, report, ctx.metrics, cell_dir)
                    _print_cell(rec)
        low = configs[0].id
        high = configs[-1].id
        summary = aggregate(records, low=low, high=high)
        summary["records"] = records
        (out_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        meta = {"run_name": out_dir.name, "model": model, "seeds": seeds,
                "tasks": [t.id for t in tasks], "configs": [c.id for c in configs]}
        report_md = render_markdown(summary, meta)
        (out_dir / "report.md").write_text(report_md, encoding="utf-8")
        logger.log_summary(summary, report_md)
    return summary


def _print_cell(rec: dict):
    scores = " ".join(f"{a[:4]}={rec['axis_scores'][a]:.2f}"
                      for a in AXES + ["combined"] if rec["axis_scores"].get(a) is not None)
    extra = f" steps2diag={rec['metrics'].get('steps_to_diagnosis')}" \
        if "steps_to_diagnosis" in rec["metrics"] else ""
    print(f"  [{rec['task']:16s} {rec['config']:3s} s{rec['seed']}] {scores} "
          f"tok={rec['tokens_total']} tools={rec['tool_calls']}{extra}")
