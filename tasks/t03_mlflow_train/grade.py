"""Notation de T03 : le CODE logue bien dans MLflow via l'env (serveur de la plateforme),
parametres ET metriques, sans tracking local en dur et sans secret."""
from bench.grading import (
    Check,
    code_contains,
    code_text,
    env_vars_used,
    file_exists,
    mlflow_tracking_not_local,
    no_hardcoded_secrets,
    references_mlflow_api,
)


def grade(ctx):
    ws = ctx.workspace
    checks = []
    checks.append(file_exists(ws, ["*.py"], name="script_present"))
    text = code_text(ws, ["*.py"])
    checks.append(references_mlflow_api(text))
    checks.append(mlflow_tracking_not_local(text))
    has_params = bool(__import__("re").search(r"log_params?\(|autolog\(", text))
    has_metrics = bool(__import__("re").search(r"log_metrics?\(|autolog\(", text))
    s = (has_params + has_metrics) / 2
    checks.append(Check("logs_params_and_metrics", s >= 0.99, s, axis="functional",
                        detail=f"params={has_params}, metrics={has_metrics}"))
    checks.append(code_contains(text, [r"set_experiment\(|experiment_name|MLFLOW_EXPERIMENT_NAME"],
                                name="sets_experiment", axis="platform"))
    # lu via l'env (pas seulement mentionne) ; ou laisse implicite a mlflow (qui lit l'env
    # lui-meme) : dans ce cas aucun set_tracking_uri ne doit apparaitre.
    env_check = env_vars_used(text, ["MLFLOW_TRACKING_URI"], name="reads_mlflow_tracking_uri")
    if not env_check.passed and text.strip() and "set_tracking_uri" not in text:
        env_check = Check("reads_mlflow_tracking_uri", True, 1.0, axis="platform",
                          detail="aucun set_tracking_uri : mlflow lit MLFLOW_TRACKING_URI lui-meme")
    checks.append(env_check)
    checks.append(no_hardcoded_secrets(text))
    return checks
