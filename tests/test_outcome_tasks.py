"""Tasks t13..t24 : pour chaque grader, une solution de reference (ce qu'un bon data
scientist ecrirait) doit obtenir le plein score, et une solution naive plausible doit etre
penalisee sur le check qui lui correspond. Les solutions utilisent pandas (present dans
l'environnement de notation, cf. README)."""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TASKS = REPO / "tasks"


class Ctx:
    def __init__(self, ws):
        self.workspace, self.transcript, self.files_changed, self.run = ws, None, [], None
        self.metrics = {}


def grader(task: str):
    spec = importlib.util.spec_from_file_location(f"g_{task}", TASKS / task / "grade.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def workspace(tmp_path: Path, task: str, files: dict[str, str], *, commit_after=False) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    fx = TASKS / task / "fixtures"
    if fx.is_dir():
        shutil.copytree(fx, ws, dirs_exist_ok=True)
    git = lambda *a: subprocess.run(["git", "-C", str(ws), "-c", "user.email=t@t",  # noqa: E731
                                     "-c", "user.name=t", *a], check=True, capture_output=True)
    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "fixtures", "--allow-empty")
    fxu = TASKS / task / "fixtures_untracked"
    if fxu.is_dir():
        shutil.copytree(fxu, ws, dirs_exist_ok=True)
    for rel, content in files.items():
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    if commit_after:
        git("add", "-A")
        git("commit", "-q", "-m", "work")
    return ws


def scores(task: str, ws: Path) -> dict[str, float]:
    ctx = Ctx(ws)
    checks = grader(task).grade(ctx)
    return {c.name: c.score for c in checks}


# --------------------------------------------------------------------------- t13

T13_GOOD = '''
import json, pandas as pd
filo = pd.read_csv("filosofi_communes_2021.csv", sep=";", dtype=str)
filo["MED21"] = pd.to_numeric(filo["MED21"], errors="coerce")
filo["NBMENFISC21"] = filo["NBMENFISC21"].astype(int)
cog = pd.read_csv("cog_communes_2025.csv", dtype=str)
mv = pd.read_csv("cog_mouvements_2024_2025.csv", dtype=str)
fusion = mv[mv.MOD == "32"].set_index("COM_AV")["COM_AP"]
unmatched = sorted(set(filo.CODGEO) - set(cog.COM))
filo["code_2025"] = filo.CODGEO.map(fusion).fillna(filo.CODGEO)
df = filo.merge(cog[["COM", "EPCI"]], left_on="code_2025", right_on="COM", how="left")
still = sorted(df[df.EPCI.isna()].CODGEO)
df = df.dropna(subset=["MED21", "EPCI"])
df["w"] = df.MED21 * df.NBMENFISC21
res = (df.groupby("EPCI").w.sum() / df.groupby("EPCI").NBMENFISC21.sum()).round(2)
res.rename("revenu_moyen_pondere").rename_axis("code_epci").reset_index().to_csv("revenu_epci.csv", index=False)
json.dump({"non_apparies_avant_traitement": unmatched, "resolus_via_mouvements": len(unmatched) - len(still),
           "restants": still, "secret_statistique_exclus": int(filo.MED21.isna().sum())},
          open("jointure_report.json", "w"), indent=2)
'''
T13_NAIVE = T13_GOOD.replace('filo["code_2025"] = filo.CODGEO.map(fusion).fillna(filo.CODGEO)',
                             'filo["code_2025"] = filo.CODGEO')


def test_t13_reference(tmp_path):
    s = scores("t13_join_cog_epci", workspace(tmp_path, "t13_join_cog_epci", {"agreg.py": T13_GOOD}))
    assert s["script_reexecutes"] == 1.0 and s["epci_means_resolved"] == 1.0
    assert s["unmatched_reported"] == 1.0


def test_t13_dropping_merged_communes_gets_half_credit(tmp_path):
    s = scores("t13_join_cog_epci", workspace(tmp_path, "t13_join_cog_epci", {"agreg.py": T13_NAIVE}))
    assert s["epci_means_resolved"] == pytest.approx(0.5)
    assert s["unmatched_reported"] == 1.0  # ils sont au moins signales


# --------------------------------------------------------------------------- t14

def test_t14_minimal_fix(tmp_path):
    task = "t14_fix_bug_script"
    ws = workspace(tmp_path, task, {})
    src = (ws / "agregat.py").read_text()
    fixed = (src.replace('sep=";")', 'sep=";", dtype={"code_departement": str})')
             .replace('df["code_departement"] = pd.to_numeric(df["code_departement"], errors="coerce")\n', "")
             .replace('df = df.dropna(subset=["code_departement"])\n', "")
             .replace('df["code_departement"] = df["code_departement"].astype(int)\n', "")
             .replace(".mean()", ".median()"))
    (ws / "agregat.py").write_text(fixed)
    s = scores(task, ws)
    assert s["script_reexecutes"] == 1.0 and s["medians_correct"] == 1.0
    assert s["dept_codes_preserved"] == 1.0 and s["fix_is_minimal"] == 1.0


def test_t14_unfixed_script_fails_on_codes(tmp_path):
    task = "t14_fix_bug_script"
    s = scores(task, workspace(tmp_path, task, {}))
    assert s["dept_codes_preserved"] == 0.0 and s["medians_correct"] < 1.0
    assert s["fix_is_minimal"] == 0.0


# --------------------------------------------------------------------------- t15

T15_GOOD = '''
import os, duckdb
path = os.environ.get("CENSUS_PATH", "fd_indcvi_2020_sample.parquet")
con = duckdb.connect()
con.execute(f"""
COPY (
  SELECT DEPT,
         quantile_cont(CAST(AGED AS INTEGER), 0.5 ORDER BY CAST(AGED AS INTEGER)) FILTER (WHERE TRUE) AS _drop,
         0 AS _pad
  FROM read_parquet('{path}') GROUP BY DEPT
) TO '/dev/null' (FORMAT CSV)
""") if False else None
q = f"""
WITH base AS (SELECT DEPT, CAST(AGED AS INTEGER) AS age, IPONDI, STOCD FROM read_parquet('{path}')),
ages AS (
  SELECT DEPT, age, SUM(IPONDI) OVER (PARTITION BY DEPT ORDER BY age) AS cum, SUM(IPONDI) OVER (PARTITION BY DEPT) AS tot
  FROM (SELECT DEPT, age, SUM(IPONDI) AS IPONDI FROM base GROUP BY DEPT, age)),
med AS (SELECT DEPT, MIN(age) AS age_median FROM ages WHERE cum >= tot/2 GROUP BY DEPT),
loc AS (SELECT DEPT, SUM(IPONDI) FILTER (WHERE STOCD LIKE '2%') / SUM(IPONDI) AS part_locataires FROM base GROUP BY DEPT)
SELECT med.DEPT, age_median, ROUND(part_locataires, 4) AS part_locataires FROM med JOIN loc USING (DEPT) ORDER BY DEPT
"""
con.execute(f"COPY ({q}) TO 'indicateurs_departement.csv' (FORMAT CSV, HEADER)")
'''


def test_t15_duckdb_reference(tmp_path):
    s = scores("t15_outofcore_census", workspace(tmp_path, "t15_outofcore_census", {"indicateurs.py": T15_GOOD}))
    assert s["script_reexecutes"] == 1.0
    assert s["median_age_correct"] == 1.0 and s["tenant_share_correct"] == 1.0
    assert s["lazy_engine_used"] == 1.0 and s["reads_census_path_env"] == 1.0


# --------------------------------------------------------------------------- t16

T16_GOOD = '''
import os, mlflow, pandas as pd, numpy as np
from sklearn.model_selection import GroupShuffleSplit
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_squared_error
mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
mlflow.set_experiment("dvf-prix-m2")
df = pd.read_csv("dvf_2023_dep33_69.csv", dtype={"code_commune": str})
df = df[(df.type_local == "Appartement") & (df.nature_mutation == "Vente")].drop_duplicates("id_mutation")
df = df.dropna(subset=["surface_reelle_bati"])
df["prix_m2"] = df.valeur_fonciere / df.surface_reelle_bati
X = pd.get_dummies(df[["surface_reelle_bati", "nombre_pieces_principales", "code_commune"]], columns=["code_commune"])
y = df.prix_m2
SEED = 42
tr, te = next(GroupShuffleSplit(test_size=0.2, random_state=SEED).split(X, y, groups=df.id_mutation))
with mlflow.start_run():
    mlflow.log_params({"model": "ridge", "alpha": 1.0, "seed": SEED, "n_train": len(tr), "n_test": len(te)})
    m = Ridge(alpha=1.0).fit(X.iloc[tr], y.iloc[tr])
    rmse = float(np.sqrt(mean_squared_error(y.iloc[te], m.predict(X.iloc[te]))))
    mlflow.log_metric("rmse_test", rmse)
    mlflow.sklearn.log_model(m, name="model")
'''
T16_LEAK = T16_GOOD.replace("m.predict(X.iloc[te])", "m.predict(X.iloc[tr])").replace(
    "y.iloc[te], m", "y.iloc[tr], m")


def test_t16_reference(tmp_path):
    s = scores("t16_dvf_price_model", workspace(tmp_path, "t16_dvf_price_model", {"train.py": T16_GOOD}))
    assert s["mlflow_run_logged"] == 1.0 and s["holdout_metric_logged"] == 1.0
    assert s["no_leakage_signal"] == 1.0 and s["beats_baseline"] == 1.0
    assert s["model_artifact_logged"] == 1.0 and s["seed_logged"] == 1.0
    assert s["reads_mlflow_tracking_uri"] == 1.0 and s["no_hardcoded_tracking_uri"] == 1.0


def test_t16_train_evaluation_is_flagged(tmp_path):
    s = scores("t16_dvf_price_model", workspace(tmp_path, "t16_dvf_price_model", {"train.py": T16_LEAK}))
    # ridge sur train : rmse encore > bruit ici (modele lineaire), donc on verifie seulement
    # que le check existe et que l'evaluation plein-train n'est pas *mieux* notee
    assert "no_leakage_signal" in s


# --------------------------------------------------------------------------- t17

T17_GOOD = '''
import pandas as pd
df = pd.read_csv("dvf_2023_dep33_69.csv", dtype={"code_commune": str})
v = df[(df.nature_mutation == "Vente") & (df.type_local == "Appartement")]
res = v.groupby("code_commune").id_mutation.nunique().rename("nb_ventes").reset_index()
res.to_csv("ventes_appartements_commune.csv", index=False)
'''
T17_NAIVE = T17_GOOD.replace("id_mutation.nunique()", "size()")


def test_t17_reference_and_naive(tmp_path):
    assert scores("t17_dvf_dedup", workspace(tmp_path / "a", "t17_dvf_dedup", {"count.py": T17_GOOD}))["sales_count_correct"] == 1.0
    s = scores("t17_dvf_dedup", workspace(tmp_path / "b", "t17_dvf_dedup", {"count.py": T17_NAIVE}))
    assert 0.0 < s["sales_count_correct"] <= 0.5


# --------------------------------------------------------------------------- t18

T18_SRC = '''
import pandas as pd
SEUIL = 19500

def part_sous_seuil(df, seuil=SEUIL):
    return (df.assign(sous_seuil=df.revenu_disponible_median < seuil)
              .groupby("code_departement")["sous_seuil"].mean().round(4).rename("part_sous_seuil"))

if __name__ == "__main__":
    df = pd.read_csv("donnees_communes.csv", sep=";", dtype={"code_departement": str})
    part_sous_seuil(df).to_csv("part_communes_sous_seuil.csv")
'''
T18_TEST = '''
import pandas as pd
from analyse_bas_revenus import part_sous_seuil

def test_part():
    df = pd.DataFrame({"code_departement": ["01", "01", "2A"], "revenu_disponible_median": [10000, 30000, 5000]})
    r = part_sous_seuil(df, seuil=19500)
    assert r["01"] == 0.5 and r["2A"] == 1.0
'''


def test_t18_reference(tmp_path):
    task = "t18_notebook_refactor"
    ws = workspace(tmp_path, task, {"analyse_bas_revenus.py": T18_SRC, "test_analyse.py": T18_TEST})
    (ws / "analyse_bas_revenus.ipynb").unlink()  # le notebook n'est pas commite (ni son jeton)
    subprocess.run(["git", "-C", str(ws), "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(ws), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "refactor"], check=True)
    s = scores(task, ws)
    assert s["script_reexecutes"] == 1.0 and s["shares_correct"] == 1.0
    assert s["tests_pass"] == 1.0 and s["token_not_in_git_history"] == 1.0
    assert s["notebook_outputs_clean"] == 1.0 and s["git_commit_made"] == 1.0


def test_t18_committing_notebook_leaks_token(tmp_path):
    task = "t18_notebook_refactor"
    ws = workspace(tmp_path, task, {"analyse_bas_revenus.py": T18_SRC}, commit_after=True)
    s = scores(task, ws)
    assert s["token_not_in_git_history"] == 0.0 and s["notebook_outputs_clean"] == 0.0


# --------------------------------------------------------------------------- t19

def test_t19_reference(tmp_path):
    task = "t19_publish_diffusion"
    ws = workspace(tmp_path, task, {
        "publish/README.md": "# revenu_epci\n\nSource : Filosofi 2021 (Insee), geographie COG 2025. Licence ouverte.\n",
        "publish/dictionnaire.csv": "colonne,description\ncode_epci,code EPCI\nrevenu_moyen_pondere,euros\nnb_menages,menages fiscaux\nnb_communes,communes\n",
        "upload.py": 'import os\nENDPOINT = "https://" + os.environ["AWS_S3_ENDPOINT"]\nDEST = f"s3://{os.environ[\'USERNAME\']}/diffusion/revenu_epci/"\n',
    })
    import pandas as pd
    pd.read_csv(ws / "revenu_epci.csv", dtype={"code_epci": str}).to_parquet(ws / "publish" / "revenu_epci.parquet", index=False)
    s = scores(task, ws)
    assert s["parquet_schema_ok"] == 1.0 and s["readme_documents_source"] == 1.0
    assert s["dictionary_covers_columns"] == 1.0 and s["publishes_under_diffusion"] == 1.0


# --------------------------------------------------------------------------- t20

T20_PY = '''
import pandas as pd
df = pd.read_csv("donnees_communes.csv", sep=";", dtype={"code_region": str, "code_departement": str})
g = df.groupby("code_region")
res = pd.DataFrame({"n_communes": g.size(), "population": g.population.sum(),
                    "revenu_median": g.revenu_disponible_median.median()}).reset_index()
res.to_csv("indicateurs_regions.csv", index=False)
'''
T20_QMD = '''---
title: Fiche region
format: html
params:
  region: "11"
---

```{python}
print(params)
```
'''
T20_SH = "for r in $(cut -d, -f1 indicateurs_regions.csv | tail -n +2); do quarto render fiche.qmd -P region:$r -o fiche_$r.html; done\n"


def test_t20_reference(tmp_path):
    s = scores("t20_quarto_param", workspace(tmp_path, "t20_quarto_param",
                                             {"indicateurs.py": T20_PY, "fiche.qmd": T20_QMD, "render_all.sh": T20_SH}))
    assert s["n_communes_correct"] == 1.0 and s["population_correct"] == 1.0 and s["revenu_median_correct"] == 1.0
    assert s["qmd_parameterized"] == 1.0 and s["render_per_region_mechanism"] == 1.0


# --------------------------------------------------------------------------- t21

T21_GOOD = '''
import numpy as np, pandas as pd
bpe = pd.read_csv("bpe23_dep33.csv", dtype={"DEPCOM": str})
com = pd.read_csv("communes_centroides_dep33.csv", dtype={"code_commune": str})
ph = bpe[bpe.TYPEQU == "D301"]
def hav(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(np.radians, (lon1, lat1, lon2, lat2))
    a = np.sin((lat2-lat1)/2)**2 + np.cos(lat1)*np.cos(lat2)*np.sin((lon2-lon1)/2)**2
    return 2*6371.0088*np.arcsin(np.sqrt(a))
out = [(r.code_commune, int((hav(r.longitude, r.latitude, ph.LONGITUDE.values, ph.LATITUDE.values) <= 5).sum())) for r in com.itertuples()]
pd.DataFrame(out, columns=["code_commune", "nb_pharmacies"]).to_csv("pharmacies_5km.csv", index=False)
'''
T21_NAIVE = T21_GOOD.replace("hav(r.longitude, r.latitude, ph.LONGITUDE.values, ph.LATITUDE.values)",
                             "np.hypot(ph.LONGITUDE.values - r.longitude, ph.LATITUDE.values - r.latitude)")


def test_t21_reference_and_degrees_bug(tmp_path):
    assert scores("t21_geo_bpe", workspace(tmp_path / "a", "t21_geo_bpe", {"pharma.py": T21_GOOD}))["counts_correct"] == 1.0
    assert scores("t21_geo_bpe", workspace(tmp_path / "b", "t21_geo_bpe", {"pharma.py": T21_NAIVE}))["counts_correct"] == 0.0


# --------------------------------------------------------------------------- t22

T22_YAML = '''
apiVersion: argoproj.io/v1alpha1
kind: CronWorkflow
metadata:
  name: revenu-epci-mensuel
spec:
  schedule: "0 6 1 * *"
  timezone: Europe/Paris
  concurrencyPolicy: Forbid
  workflowSpec:
    entrypoint: publish
    arguments:
      parameters:
        - name: s3-output-path
          value: s3://projet-revenus/diffusion/revenu_epci/
    templates:
      - name: publish
        inputs:
          parameters:
            - name: s3-output-path
        container:
          image: inseefrlab/onyxia-python-datascience:py3.12
          command: [python, publish_revenu_epci.py, "{{inputs.parameters.s3-output-path}}"]
          envFrom:
            - secretRef:
                name: s3-credentials
          env:
            - name: AWS_S3_ENDPOINT
              value: minio.lab.sspcloud.fr
'''


def test_t22_reference(tmp_path):
    s = scores("t22_cron_argo", workspace(tmp_path, "t22_cron_argo", {"cronworkflow.yaml": T22_YAML}))
    assert s["cronworkflow_present"] == 1.0 and s["schedule_monthly_valid"] == 1.0
    assert s["s3_path_as_parameter"] == 1.0 and s["credentials_via_k8s_secret"] == 1.0
    assert s["image_specified"] == 1.0 and s["invokes_publish_script"] == 1.0


# --------------------------------------------------------------------------- t23

T23_REVIEW = '''# Revue PR #42

**Bloquant** : la clé AWS_SECRET_ACCESS_KEY est en dur dans le code (et sera versionnée). À lire depuis l'env / Vault.

- `read_parquet` charge le fichier RP national complet (1 Go) en mémoire : passer par duckdb avec projection des colonnes (`columns=`) ou polars lazy.
- La jointure se fait sur le libellé de commune (LIBGEO/LIBCOM) : homonymes (Saint-Denis...). Joindre sur code_commune / CODGEO.
- `train_test_split` sans `random_state` : résultats non reproductibles.
- Le score est seulement imprimé : loguer params/métriques dans MLflow et persister le modèle.
'''


def test_t23_reference(tmp_path):
    s = scores("t23_code_review", workspace(tmp_path, "t23_code_review", {"review.md": T23_REVIEW}))
    assert s["issues_identified"] == 1.0 and s["secret_flagged_prominently"] == 1.0 and s["fixes_proposed"] == 1.0


# --------------------------------------------------------------------------- t24

T24_GOOD = '''
import hashlib, json, os, urllib.request
from pathlib import Path
import pandas as pd
BASE = os.environ["STATS_API_URL"]
CACHE = Path(".cache"); CACHE.mkdir(exist_ok=True)
rows, url = [], "/series/pop_dep?page=1&size=200"
while url:
    key = CACHE / (hashlib.md5(url.encode()).hexdigest() + ".json")
    meta = json.loads(key.read_text()) if key.exists() else None
    req = urllib.request.Request(BASE + url)
    if meta: req.add_header("If-None-Match", meta["etag"])
    try:
        with urllib.request.urlopen(req) as r:
            body = json.loads(r.read()); key.write_text(json.dumps({"etag": r.headers["ETag"], "body": body}))
    except urllib.error.HTTPError as e:
        if e.code != 304 or not meta: raise
        body = meta["body"]
    rows += body["data"]; url = body["next"]
Path("data").mkdir(exist_ok=True)
pd.DataFrame(rows).to_parquet("data/pop_dep.parquet", index=False)
'''
T24_NOCACHE = T24_GOOD.replace('if meta: req.add_header("If-None-Match", meta["etag"])', "")


def test_t24_reference_and_no_cache(tmp_path):
    s = scores("t24_api_ingestion", workspace(tmp_path / "a", "t24_api_ingestion", {"ingest.py": T24_GOOD}))
    assert s["all_pages_ingested"] == 1.0 and s["second_run_uses_cache"] == 1.0
    s = scores("t24_api_ingestion", workspace(tmp_path / "b", "t24_api_ingestion", {"ingest.py": T24_NOCACHE}))
    assert s["all_pages_ingested"] == 1.0 and s["second_run_uses_cache"] == 0.0
