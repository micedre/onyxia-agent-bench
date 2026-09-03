"""Etape 2 : entrainement d'un modele lineaire population -> revenu_disponible"""
import json

import pandas as pd
from sklearn.linear_model import LinearRegression

df = pd.read_parquet("data/clean.parquet")
model = LinearRegression().fit(df[["population"]], df["revenu_disponible"])
metrics = {"r2": float(model.score(df[["population"]], df["revenu_disponible"])),
           "coef": float(model.coef_[0]), "intercept": float(model.intercept_)}
json.dump(metrics, open("data/metrics.json", "w"))
print(metrics)
