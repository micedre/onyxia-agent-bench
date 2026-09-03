# FIXTURE DE BENCHMARK - CODE VOLONTAIREMENT DEFECTUEUX, NE PAS REUTILISER.
# La cle ci-dessous est la cle d'EXEMPLE publiee par AWS dans sa documentation, pas
# un identifiant reel ; elle est en dur expres, c'est l'un des cinq problemes que la
# tache t23 demande de reperer en relecture. Si un scanner de secrets la signale,
# c'est ce fichier qu'il faut mettre en liste d'exclusion.
"""Pipeline hebdo : revenu par EPCI a partir du RP et de Filosofi."""
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.model_selection import train_test_split

AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"  # cle du compte projet
ENDPOINT = "https://minio.lab.sspcloud.fr"


def load():
    rp = pd.read_parquet("s3://donnees-insee/diffusion/RP/fd_indcvi_2020.parquet",
                         storage_options={"client_kwargs": {"endpoint_url": ENDPOINT},
                                          "secret": AWS_SECRET_ACCESS_KEY})
    filo = pd.read_csv("filosofi_communes_2021.csv", sep=";")
    return rp, filo


def build(rp, filo):
    pop = rp.groupby("LIBCOM")["IPONDI"].sum().rename("population")
    df = filo.merge(pop, left_on="LIBGEO", right_on="LIBCOM", how="inner")
    return df


def train(df):
    X, y = df[["population"]], df["MED21"]
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2)
    model = Ridge().fit(Xtr, ytr)
    print("score", model.score(Xte, yte))
    return model


if __name__ == "__main__":
    rp, filo = load()
    train(build(rp, filo))
