"""Revenu disponible median par departement (script hebdo, tourne depuis 2023)."""
import pandas as pd

df = pd.read_csv("donnees_communes.csv", sep=";")
# normalisation du code departement pour le tri numerique
df["code_departement"] = pd.to_numeric(df["code_departement"], errors="coerce")
df = df.dropna(subset=["code_departement"])
df["code_departement"] = df["code_departement"].astype(int)

res = (df.groupby("code_departement")["revenu_disponible_median"]
         .mean().round(0).rename("revenu_median").reset_index()
         .sort_values("code_departement"))
res.to_csv("revenu_median_departement.csv", index=False)
print(res)
