"""Recalcule revenu_epci.parquet et l'ecrit sur S3.
Usage : python publish_revenu_epci.py s3://bucket/diffusion/revenu_epci/"""
import os
import sys

import pandas as pd

if __name__ == "__main__":
    dest = sys.argv[1]
    endpoint = "https://" + os.environ["AWS_S3_ENDPOINT"]
    df = pd.read_csv("revenu_epci.csv")
    df.to_parquet(dest.rstrip("/") + "/revenu_epci.parquet",
                  storage_options={"client_kwargs": {"endpoint_url": endpoint}})
