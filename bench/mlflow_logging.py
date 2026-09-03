"""Journalisation MLflow : run parent (invocation) + runs enfants (cellules).

URI de tracking : MLFLOW_TRACKING_URI si defini (serveur plateforme), sinon SQLite local.
Si mlflow n'est pas installe, on bascule sur un logger nul (le benchmark tourne quand meme).

Implemente avec `MlflowClient` et des run_id explicites (pas l'API fluent) : les runs enfants
portent le tag `mlflow.parentRunId` et sont donc bien imbriques sous le run parent dans l'UI
(avec l'API fluent en multi-thread ils apparaissaient orphelins - 1183 runs a plat sur le
serveur), et le run parent porte les parametres de l'invocation (modele, configs, seeds...).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

from bench.schema import GradeReport, RunResult, combined_score

try:
    import mlflow  # type: ignore
    from mlflow.entities import Metric, Param, RunTag  # type: ignore
    from mlflow.tracking import MlflowClient  # type: ignore
    _HAS_MLFLOW = True
except Exception:  # pragma: no cover
    _HAS_MLFLOW = False

_PARAM_MAX = 500  # limite historique des valeurs de param cote serveur


def resolve_tracking_uri(repo_root: Path) -> str:
    # MLflow 3.x a retire le file store ; en local on utilise SQLite (recommande).
    # En prod, MLFLOW_TRACKING_URI pointe sur le serveur de la plateforme.
    return os.environ.get("MLFLOW_TRACKING_URI") or f"sqlite:///{(repo_root / 'mlflow.db').as_posix()}"


def _p(v) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s[:_PARAM_MAX]


class NullLogger:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def log_meta(self, *a, **k):
        pass

    def log_cell(self, *a, **k):
        pass

    def log_summary(self, *a, **k):
        pass


class MlflowLogger:
    def __init__(self, repo_root: Path, experiment: str, run_name: str):
        self.repo_root = repo_root
        self.experiment = experiment
        self.run_name = run_name
        self.client = None
        self.parent_id = None
        self.experiment_id = None

    def __enter__(self):
        mlflow.set_tracking_uri(resolve_tracking_uri(self.repo_root))
        self.client = MlflowClient()
        exp = self.client.get_experiment_by_name(self.experiment)
        self.experiment_id = exp.experiment_id if exp else self.client.create_experiment(self.experiment)
        run = self.client.create_run(self.experiment_id, run_name=self.run_name,
                                     tags={"bench.kind": "parent"})
        self.parent_id = run.info.run_id
        self._parent_status = "FINISHED"
        print(f"[mlflow] run parent {self.run_name} -> {self.parent_id}")
        return self

    def __exit__(self, exc_type, *a):
        if self.parent_id:
            self.client.set_terminated(self.parent_id,
                                       status="FAILED" if exc_type else self._parent_status)
        return False

    def log_meta(self, meta: dict):
        params = {k: _p(v) for k, v in meta.items() if v is not None}
        try:
            params["git_commit"] = subprocess.run(
                ["git", "-C", str(self.repo_root), "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True).stdout.strip() or "?"
        except OSError:
            pass
        ts = int(time.time() * 1000)
        self.client.log_batch(self.parent_id, params=[Param(k, v) for k, v in params.items()],
                              tags=[RunTag("bench.kind", "parent")])
        _ = ts

    def log_cell(self, run: RunResult, report: GradeReport, extra_metrics: dict,
                 artifacts_dir: Path):
        rid = f"{run.task_id}/{run.config_id}/seed{run.seed}"
        child = self.client.create_run(
            self.experiment_id, run_name=rid,
            tags={"mlflow.parentRunId": self.parent_id, "bench.kind": "cell",
                  "bench.task": run.task_id, "bench.config": run.config_id,
                  "bench.status": run.status})
        cid = child.info.run_id
        params = {
            "task": run.task_id, "config": run.config_id, "model": run.model,
            "seed": run.seed, "exit_code": run.exit_code, "timed_out": int(run.timed_out),
            "status": run.status, "error": (run.error or "")[:_PARAM_MAX],
        }
        t = run.transcript
        metrics = {
            "tokens_total": t.tokens_total, "tokens_in": t.tokens_in, "tokens_out": t.tokens_out,
            "tokens_reasoning": t.tokens_reasoning,
            "context_tokens_first": t.context_tokens_first,
            "context_tokens_last": t.context_tokens_last,
            "cost": t.cost,
            "tool_calls": len(t.tool_events), "assistant_turns": t.assistant_turns,
            "permission_rejections": t.permission_rejections, "tool_errors": t.tool_errors,
            "subagent_calls": t.subagent_calls,
            "wall_clock_s": round(run.wall_clock_s, 3), "agent_s": round(run.agent_s, 3),
            "steps": t.assistant_turns or len(t.events),
            "safety_violations": report.safety_violations,
            "valid": int(run.status in ("ok", "timeout")),
        }
        axis_scores = report.to_dict()["axis_scores"]
        for axis, val in axis_scores.items():
            if val is not None:
                metrics[f"score_{axis}"] = round(val, 4)
        cs = combined_score(axis_scores)
        if cs is not None:
            metrics["score_combined"] = cs
        metrics.update({k: v for k, v in extra_metrics.items()
                        if isinstance(v, (int, float)) and not isinstance(v, bool)})
        ts = int(time.time() * 1000)
        self.client.log_batch(
            cid, params=[Param(k, _p(v)) for k, v in params.items()],
            metrics=[Metric(k, float(v), ts, 0) for k, v in metrics.items()])
        if artifacts_dir.exists():
            try:
                # transcript/rapport/diag : petits fichiers, previsualisables direct dans l'UI.
                for fname in ("transcript.json", "grade_report.json", "raw.ndjson",
                              "k8s_failure.txt"):
                    fp = artifacts_dir / fname
                    if fp.is_file():
                        self.client.log_artifact(cid, str(fp), artifact_path=rid)
                # ws/ : zippe en un seul artefact plutot qu'un fichier par fichier
                # (un workspace peut contenir des dizaines/milliers de fichiers selon la
                # config/tache ; chaque upload MLflow est une requete HTTP separee).
                ws = artifacts_dir / "ws"
                if ws.is_dir():
                    zip_path = artifacts_dir / "ws.zip"
                    try:
                        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                            for p in ws.rglob("*"):
                                if p.is_file() and ".venv" not in p.parts:
                                    zf.write(p, p.relative_to(artifacts_dir))
                        self.client.log_artifact(cid, str(zip_path), artifact_path=rid)
                    finally:
                        zip_path.unlink(missing_ok=True)
            except Exception as e:  # artefacts optionnels : ne pas casser le run
                print(f"[warn] log_artifacts ignore: {e}")
        self.client.set_terminated(cid, status="FINISHED" if run.status in ("ok", "timeout")
                                   else "FAILED")

    def log_summary(self, summary: dict, report_md: str | None = None):
        # deltas (dont delta_combined), scores combines et fiabilite par config, sur le parent
        flat = {}
        for axis, val in summary.get("delta_by_axis", {}).items():
            if val is not None:
                flat[f"delta_{axis}"] = round(val, 4)
        dp = summary.get("delta_combined_paired")
        if dp:
            flat["delta_combined_paired"] = dp["mean"]
            flat["delta_combined_paired_ci_low"], flat["delta_combined_paired_ci_high"] = dp["ci95"]
        for cfg, axes in summary.get("mean_by_config", {}).items():
            for axis, val in axes.items():
                if val is not None:
                    flat[f"{axis}_{cfg}"] = round(val, 4)
        for cfg, rel in summary.get("reliability", {}).items():
            flat[f"n_valid_{cfg}"] = rel["n_valid"]
            if rel.get("timeout_rate") is not None:
                flat[f"timeout_rate_{cfg}"] = rel["timeout_rate"]
            for k in ("mean_tokens_total", "mean_agent_s", "mean_permission_rejections"):
                if rel.get(k) is not None:
                    flat[f"{k}_{cfg}"] = rel[k]
        flat["n_valid"] = summary.get("n_valid", 0)
        ts = int(time.time() * 1000)
        if flat:
            self.client.log_batch(self.parent_id,
                                  metrics=[Metric(k, float(v), ts, 0) for k, v in flat.items()])
        tmp_dir = self.repo_root / "_mlflow_upload_tmp"
        tmp_dir.mkdir(exist_ok=True)
        try:
            p = tmp_dir / "summary.json"
            p.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
            self.client.log_artifact(self.parent_id, str(p), artifact_path="summary")
            if report_md:
                rp = tmp_dir / "report.md"
                rp.write_text(report_md, encoding="utf-8")
                self.client.log_artifact(self.parent_id, str(rp), artifact_path="summary")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def make_logger(repo_root: Path, experiment: str, run_name: str, enabled: bool):
    if enabled and _HAS_MLFLOW:
        return MlflowLogger(repo_root, experiment, run_name)
    return NullLogger()


HAS_MLFLOW = _HAS_MLFLOW
