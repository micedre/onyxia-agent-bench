"""Revenu moyen des menages par tranche d'age du referent."""
import pandas as pd

df = pd.read_csv("data/enquete_menages.csv")
df["tranche"] = pd.cut(df["age_referent"], [0, 30, 50, 70, 120])
print(df.groupby("tranche", observed=True)["revenu_menage"].mean().round(0))
