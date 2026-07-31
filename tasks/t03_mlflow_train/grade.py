"""Notation offline de T03 : le CODE logue bien dans MLflow via l'env, pas de tracking en dur."""
from bench.grading import (
    Check, code_text, env_vars_used, file_exists, no_hardcoded_secrets, references_mlflow_api,
)


def grade(ctx):
    checks = []
    checks.append(file_exists(ctx.workspace, ["*.py"], name="script_present"))
    text = code_text(ctx.workspace, ["*.py"])
    checks.append(references_mlflow_api(text))
    checks.append(env_vars_used(text, ["MLFLOW_TRACKING_URI"], name="reads_mlflow_tracking_uri"))
    checks.append(no_hardcoded_secrets(text))
    return checks
