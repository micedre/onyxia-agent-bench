"""Notation de T28 : chaine EDA -> modele -> rapport, jugee sur des proprietes verifiables
statiquement (le modele n'est pas re-entraine, MLflow n'est pas joignable depuis la notation).

functional : un script, un rapport (.qmd / .md / .html), des chiffres issus d'un calcul
platform   : MLflow via l'API et le serveur de l'environnement, rapport Quarto, nom d'experience
repro      : projet fige (uv.lock ou renv.lock) ; rapport qui calcule ses chiffres au rendu
safety     : aucun secret en dur"""
from __future__ import annotations

import re

from bench.grading import (
    Check,
    code_contains,
    code_text,
    deliverable_files,
    env_vars_used,
    file_exists,
    mlflow_tracking_not_local,
    no_hardcoded_secrets,
    references_mlflow_api,
)

MODEL_RE = (r"LinearRegression|Ridge|Lasso|RandomForest|GradientBoosting|statsmodels|OLS\(|"
            r"sklearn|\blm\(|glm\(|lme4|ranger|xgboost|tidymodels")
CODE = ["*.py", "*.R", "*.r", "*.qmd", "*.Rmd"]


def _report_computes_figures(ws) -> Check:
    """Le rapport doit CALCULER ses chiffres : au moins un chunk executable qui lit les donnees
    (ou lit un artefact du pipeline), pas des valeurs recopiees dans du texte."""
    qmds = deliverable_files(ws, ["*.qmd", "*.Rmd"])
    if not qmds:
        mds = deliverable_files(ws, ["*.md", "*.html"])
        return Check("report_computes_figures", False, 0.0, axis="repro",
                     detail="rapport statique (pas de .qmd/.Rmd)" if mds else "aucun rapport")
    for q in qmds:
        t = q.read_text(encoding="utf-8", errors="replace")
        chunk = re.search(r"^```\{(python|r)\b", t, re.MULTILINE)
        reads = re.search(r"read_csv|read_parquet|read\.csv|read_csv\(|readRDS|json\.load|"
                          r"duckdb|mlflow", t)
        if chunk and reads:
            return Check("report_computes_figures", True, 1.0, axis="repro",
                         detail=f"{q.name}: chunk executable + lecture de donnees")
    return Check("report_computes_figures", False, 0.5, axis="repro",
                 detail="rapport Quarto sans chunk qui lit les donnees")


def grade(ctx):
    ws = ctx.workspace
    checks = []
    checks.append(file_exists(ws, ["*.py", "*.R", "*.r"], name="script_present"))
    checks.append(file_exists(ws, ["*.qmd", "*.Rmd", "*.md", "*.html"], name="report_present"))
    text = code_text(ws, CODE)
    checks.append(code_contains(text, [MODEL_RE], name="model_trained"))

    checks.append(references_mlflow_api(text))
    checks.append(mlflow_tracking_not_local(text))
    env = env_vars_used(text, ["MLFLOW_TRACKING_URI"], name="reads_mlflow_tracking_uri")
    if not env.passed and text.strip() and "set_tracking_uri" not in text:
        env = Check("reads_mlflow_tracking_uri", True, 1.0, axis="platform",
                    detail="aucun set_tracking_uri : mlflow lit MLFLOW_TRACKING_URI lui-meme")
    checks.append(env)
    checks.append(code_contains(text, [r"set_experiment\(|experiment_name|MLFLOW_EXPERIMENT_NAME"],
                                name="sets_experiment", axis="platform"))
    qmd = bool(deliverable_files(ws, ["*.qmd"]))
    checks.append(Check("report_is_quarto", qmd, 1.0 if qmd else 0.0, axis="platform",
                        detail=".qmd livre" if qmd else "pas de .qmd (convention : Quarto)"))

    lock = file_exists(ws, ["uv.lock", "renv.lock"], name="lockfile", axis="repro")
    checks.append(lock)
    checks.append(_report_computes_figures(ws))
    checks.append(no_hardcoded_secrets(text))
    return checks
