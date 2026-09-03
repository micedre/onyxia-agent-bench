"""Analyse communale : revenu disponible median par departement."""
import pandas as pd

df = pd.read_csv("donnees_insee.csv")
df = df[df["population"] > 0]
resume = df.groupby("departement")["revenu_disponible"].median()
print(resume)
