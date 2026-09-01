---
name: mlflow-tracking
description: Track experiments and manage models with the shared MLflow instance on Onyxia/SSP Cloud — logging parameters/metrics/artifacts, autolog, model signature, programmatic run comparison (search_runs), model registry and loading, in Python and R. Load whenever training a model, comparing runs, or when the task mentions tracking, experiment, MLFLOW_TRACKING_URI, registry or model versioning. (keywords: suivi d'expériences, journaliser, registre de modèles, versionnage de modèles)
license: MIT
---

# Experiment tracking with MLflow on Onyxia

Launch the **MLflow** service from the catalog: it automatically sets
`MLFLOW_TRACKING_URI` (and `MLFLOW_S3_ENDPOINT_URL`) in services launched afterwards.
Metadata goes to PostgreSQL, artifacts to MinIO.

## Check the configuration
```python
import os, mlflow
print(os.environ.get("MLFLOW_TRACKING_URI"))   # must be set
mlflow.set_experiment("project-name")           # creates/selects the experiment
```
If `MLFLOW_TRACKING_URI` is missing, point manually to the MLflow service URL
(of the form `https://user-<namespace>-<id>.user.lab.sspcloud.fr`).

## Python — manual logging
```python
import mlflow
from mlflow.models import infer_signature

with mlflow.start_run(run_name="rf-baseline"):
    mlflow.log_params({"n_estimators": 200, "max_depth": 8})
    # ... training ...
    mlflow.log_metric("f1", f1)
    mlflow.log_metric("roc_auc", auc)
    # signature + input_example: input/output schema validated at load time
    mlflow.sklearn.log_model(model, artifact_path="model",
                             registered_model_name="my_model",
                             signature=infer_signature(X_train, model.predict(X_train)),
                             input_example=X_train.head(3))
    mlflow.log_artifact("figures/confusion_matrix.png")
```

## Python — autolog (the simplest)
```python
import mlflow
mlflow.sklearn.autolog()      # or xgboost / lightgbm / pytorch / keras
mlflow.set_experiment("project-name")
with mlflow.start_run():
    model.fit(X_train, y_train)   # params, metrics and model logged automatically
```

## Python — compare and diagnose runs
```python
import mlflow
runs = mlflow.search_runs(experiment_names=["project-name"],
                          order_by=["metrics.f1 DESC"], max_results=10)
best = runs.iloc[0]          # pandas DataFrame: run_id, params.*, metrics.*
print(best["run_id"], best["metrics.f1"])

# Filter with SQL-like syntax: only finished runs of a given family
runs = mlflow.search_runs(
    experiment_names=["project-name"],
    filter_string="status = 'FINISHED' and params.model_type = 'rf' and metrics.f1 > 0.8",
)

# What differs between the best and worst runs? (first thing to check
# when a metric regresses: which params actually changed)
param_cols = [c for c in runs.columns if c.startswith("params.")]
best, worst = runs.iloc[0], runs.iloc[-1]
diverging = {c: (best[c], worst[c]) for c in param_cols if best[c] != worst[c]}
print(diverging)
```
Diagnosis reflexes when a run looks wrong:
- metric worse than baseline but params identical → suspect the **data** (log
  the S3 path + hash to make this checkable) or an unpinned dependency;
- metric suspiciously perfect (≈1.0) → target leakage in the features;
- runs missing from the comparison → check `status` (`FAILED`/`KILLED` runs
  are excluded by the filter above, not gone).

## Model registry: promote and load
```python
from mlflow import MlflowClient
client = MlflowClient()
# Load a version by alias/stage
model = mlflow.pyfunc.load_model("models:/my_model@production")
preds = model.predict(X_new)
```
Version explicitly (aliases `@champion`/`@production`) rather than overwriting
a version; keep traceability from data → run → deployed model.

Promotion checklist before moving an alias to a new version:
1. metrics ≥ current production version on the **same** evaluation data;
2. `signature` + `input_example` present (load-time schema validation);
3. run links back to the exact code (Git commit tag) and data (S3 path/hash);
4. the model loads and predicts in a fresh environment
   (`mlflow.pyfunc.load_model` in a clean venv, not your training session);
5. deployment follows GitOps, not a manual copy (see `argo-mlops`).

## R — mlflow package
R support is more limited than Python support (no autolog, reduced registry
API): for advanced usage, prefer the MLflow UI or a helper Python script.
```r
library(mlflow)
mlflow_set_experiment("project-name")
with(mlflow_start_run(), {
  mlflow_log_param("alpha", 0.3)
  # ... training ...
  mlflow_log_metric("rmse", rmse)
  mlflow_log_model(model, "model")
})
```

## Troubleshooting / common errors
- `MLFLOW_TRACKING_URI` missing → no MLflow service was running when the current
  service was created: launch MLflow from the catalog then restart the service,
  or point manually to its URL.
- `403 AccessDenied` when writing an artifact → expired S3 token (7 days),
  see the `onyxia-storage-s3` skill.

## Best practices
- One **experiment per business problem**, one **run per configuration**.
- Systematically log: code version (Git commit), dataset (S3 path
  + optional hash), seed, environment (`uv.lock`/`renv.lock`), script
  parameters (arguments, configuration file).
- Always provide `signature` and `input_example` to `log_model`: the input
  schema is then validated at load/serving time.
- To compare many configurations in parallel → hand over to the Argo
  workflow (see the `argo-mlops` skill).
