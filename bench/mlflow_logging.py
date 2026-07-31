"""Journalisation MLflow : run parent (invocation) + runs enfants (cellules).

URI de tracking : MLFLOW_TRACKING_URI si defini (serveur plateforme), sinon file:./mlruns.
Si mlflow n'est pas installe, on bascule sur un logger nul (le benchmark tourne quand meme).
"""
from __future__ import annotations

import json
import os
import shutil
import zipfile
from pathlib import Path

from bench.schema import GradeReport, RunResult, combined_score

try:
    import mlflow  # type: ignore
    _HAS_MLFLOW = True
except Exception:  # pragma: no cover
    _HAS_MLFLOW = False


def resolve_tracking_uri(repo_root: Path) -> str:
    # MLflow 3.x a retire le file store ; en local on utilise SQLite (recommande).
    # En prod, MLFLOW_TRACKING_URI pointe sur le serveur de la plateforme.
    return os.environ.get("MLFLOW_TRACKING_URI") or f"sqlite:///{(repo_root / 'mlflow.db').as_posix()}"


class NullLogger:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def log_cell(self, *a, **k):
        pass

    def log_summary(self, *a, **k):
        pass


class MlflowLogger:
    def __init__(self, repo_root: Path, experiment: str, run_name: str):
        self.repo_root = repo_root
        self.experiment = experiment
        self.run_name = run_name

    def __enter__(self):
        mlflow.set_tracking_uri(resolve_tracking_uri(self.repo_root))
        mlflow.set_experiment(self.experiment)
        self._parent = mlflow.start_run(run_name=self.run_name)
        return self

    def __exit__(self, *a):
        mlflow.end_run()
        return False

    def log_cell(self, run: RunResult, report: GradeReport, extra_metrics: dict,
                 artifacts_dir: Path):
        rid = f"{run.task_id}/{run.config_id}/seed{run.seed}"
        with mlflow.start_run(run_name=rid, nested=True):
            mlflow.log_params({
                "task": run.task_id, "config": run.config_id, "model": run.model,
                "seed": run.seed, "exit_code": run.exit_code,
                "timed_out": int(run.timed_out),
            })
            metrics = {
                "tokens_total": run.transcript.tokens_total,
                "tool_calls": len(run.transcript.tool_events),
                "wall_clock_s": round(run.wall_clock_s, 3),
                "steps": len(run.transcript.events),
                "safety_violations": report.safety_violations,
            }
            axis_scores = report.to_dict()["axis_scores"]
            for axis, val in axis_scores.items():
                if val is not None:
                    metrics[f"score_{axis}"] = round(val, 4)
            cs = combined_score(axis_scores)
            if cs is not None:
                metrics["score_combined"] = cs
            metrics.update({k: v for k, v in extra_metrics.items()
                            if isinstance(v, (int, float))})
            mlflow.log_metrics(metrics)
            if artifacts_dir.exists():
                try:
                    # transcript/rapport : petits fichiers, previsualisables direct dans l'UI.
                    for fname in ("transcript.json", "grade_report.json"):
                        fp = artifacts_dir / fname
                        if fp.is_file():
                            mlflow.log_artifact(str(fp), artifact_path=rid)
                    # ws/ : zippe en un seul artefact plutot qu'un fichier par fichier
                    # (un workspace peut contenir des dizaines/milliers de fichiers selon la
                    # config/tache ; chaque upload MLflow est une requete HTTP separee).
                    ws = artifacts_dir / "ws"
                    if ws.is_dir():
                        zip_path = artifacts_dir / "ws.zip"
                        try:
                            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                                for p in ws.rglob("*"):
                                    if p.is_file():
                                        zf.write(p, p.relative_to(artifacts_dir))
                            mlflow.log_artifact(str(zip_path), artifact_path=rid)
                        finally:
                            zip_path.unlink(missing_ok=True)
                except Exception as e:  # artefacts optionnels : ne pas casser le run
                    print(f"[warn] log_artifacts ignore: {e}")

    def log_summary(self, summary: dict, report_md: str | None = None):
        # deltas (dont delta_combined) et scores combines par config, sur le run parent
        flat = {}
        for axis, val in summary.get("delta_by_axis", {}).items():
            if val is not None:
                flat[f"delta_{axis}"] = round(val, 4)
        for cfg, axes in summary.get("mean_by_config", {}).items():
            if axes.get("combined") is not None:
                flat[f"combined_{cfg}"] = round(axes["combined"], 4)
        if flat:
            mlflow.log_metrics(flat)
        tmp_dir = self.repo_root / "_mlflow_upload_tmp"
        tmp_dir.mkdir(exist_ok=True)
        try:
            p = tmp_dir / "summary.json"
            p.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
            mlflow.log_artifact(str(p), artifact_path="summary")
            if report_md:
                rp = tmp_dir / "report.md"
                rp.write_text(report_md, encoding="utf-8")
                mlflow.log_artifact(str(rp), artifact_path="summary")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def make_logger(repo_root: Path, experiment: str, run_name: str, enabled: bool):
    if enabled and _HAS_MLFLOW:
        return MlflowLogger(repo_root, experiment, run_name)
    return NullLogger()


HAS_MLFLOW = _HAS_MLFLOW
