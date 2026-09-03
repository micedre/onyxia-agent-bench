"""Etape 3 : log des metriques dans le MLflow de la plateforme (MLFLOW_TRACKING_URI via l'env)"""
import json

import mlflow

metrics = json.load(open("data/metrics.json"))
mlflow.set_experiment("census-revenu")
with mlflow.start_run():
    mlflow.log_param("model", "LinearRegression")
    mlflow.log_metrics(metrics)
print("logged")
