"""Etape 1 : preparation des donnees (nettoyage) -> data/clean.parquet"""
import os

import pandas as pd

src = os.environ.get("INPUT_CSV", "donnees_insee.csv")
df = pd.read_csv(src, dtype={"departement": str})
df = df[df["population"] > 0].dropna(subset=["revenu_disponible"])
os.makedirs("data", exist_ok=True)
df.to_parquet("data/clean.parquet", index=False)
print(f"{len(df)} lignes propres")
