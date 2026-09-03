"""Tasks t13..t24 : pour chaque grader, une solution de reference (ce qu'un bon data
scientist ecrirait) doit obtenir le plein score, et une solution naive plausible doit etre
penalisee sur le check qui lui correspond. Les solutions utilisent pandas (present dans
l'environnement de notation, cf. README)."""
from __future__ import annotations

import importlib.util
import itertools
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


_WS_SEQ = itertools.count()


def workspace(tmp_path: Path, task: str, files: dict[str, str], *, commit_after=False) -> Path:
    """Workspace de cellule : `fixtures/` copiees puis commitees, `fixtures_untracked/`
    deposees APRES le commit - meme ordre que `bench.runner._init_workspace`. Un repertoire
    distinct par appel, pour qu'un test puisse comparer plusieurs solutions."""
    ws = tmp_path / f"cell{next(_WS_SEQ)}" / "ws"
    ws.mkdir(parents=True)
    fx = TASKS / task / "fixtures"
    if fx.is_dir():
        shutil.copytree(fx, ws, dirs_exist_ok=True)
    git = lambda *a: subprocess.run(["git", "-C", str(ws), "-c", "user.email=t@t",
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


def _checks(task: str, ws: Path):
    return grader(task).grade(Ctx(ws))


def scores(task: str, ws: Path) -> dict[str, float]:
    return {c.name: c.score for c in _checks(task, ws)}


def axes(task: str, ws: Path) -> dict[str, str]:
    """Axe de chaque check : sert a verifier qu'un check est NEUTRE (`skipped`) et non
    simplement a zero."""
    return {c.name: c.axis for c in _checks(task, ws)}


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
    assert s["fix_is_minimal"] == 0.0, "script non modifie : pas de correctif"


def _t14_fix(ws, *, dtype: bool, median: bool) -> None:
    src = (ws / "agregat.py").read_text()
    if dtype:
        src = (src.replace('sep=";")', 'sep=";", dtype={"code_departement": str})')
               .replace('df["code_departement"] = pd.to_numeric('
                        'df["code_departement"], errors="coerce")\n', "")
               .replace('df = df.dropna(subset=["code_departement"])\n', "")
               .replace('df["code_departement"] = df["code_departement"].astype(int)\n', ""))
    if median:
        src = src.replace(".mean()", ".median()")
    (ws / "agregat.py").write_text(src)


def test_t14_second_bug_is_actually_measured(tmp_path):
    """Le fichier s'appelle `revenu_median_departement.csv` mais le script calcule une
    moyenne. Avec la tolerance relative de 1 % d'origine, cet ecart restait sous le seuil dans
    9 departements sur 13 : corriger le seul bug de type notait 0.69 et ne rien corriger 0.54.
    La comparaison est desormais absolue (+/-1 EUR), donc le second bug se voit."""
    task = "t14_fix_bug_script"
    ws_partial = workspace(tmp_path, task, {})
    _t14_fix(ws_partial, dtype=True, median=False)
    partial = scores(task, ws_partial)

    ws_full = workspace(tmp_path, task, {})
    _t14_fix(ws_full, dtype=True, median=True)
    full = scores(task, ws_full)

    assert full["medians_correct"] == 1.0
    assert partial["dept_codes_preserved"] == 1.0, "le premier bug est bien corrige"
    assert partial["medians_correct"] <= 0.1, (
        f"la moyenne doit etre distinguee de la mediane (obtenu {partial['medians_correct']})")


# --------------------------------------------------------------------------- t15


# --------------------------------------------------------------------------- t16


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
    # Le notebook a ete supprime : `notebook_outputs_clean` est NEUTRE (axe skipped, score 0),
    # ni recompense ni sanction - auparavant "supprimer la piece a conviction" valait 1.0.
    assert s["notebook_outputs_clean"] == 0.0
    assert axes(task, ws)["notebook_outputs_clean"] == "skipped"
    assert s["git_commit_made"] == 1.0


def test_t18_committing_notebook_leaks_token(tmp_path):
    task = "t18_notebook_refactor"
    ws = workspace(tmp_path, task, {"analyse_bas_revenus.py": T18_SRC}, commit_after=True)
    s = scores(task, ws)
    assert s["token_not_in_git_history"] == 0.0 and s["notebook_outputs_clean"] == 0.0


# --------------------------------------------------------------------------- t19


# --------------------------------------------------------------------------- t20


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
    assert s["issues_identified"] == 1.0 and s["secret_reported"] == 1.0
    assert s["fixes_proposed"] == 1.0


# --------------------------------------------------------------------------- t24


# Les tests des taches t15/t16/t19/t20/t22/t24 vivent dans la branche
# `wip/outcome-tasks-palier2-3` avec les taches correspondantes : elles demandent une reprise
# de conception (piege non note pour t15, faux positifs de fuite pour t16, reexecution
# destructive et rendu R pour t20, journal de requetes partage pour t24) avant d'entrer dans
# le jeu de taches par defaut.


def test_t23_secret_check_is_about_the_finding_not_its_position(tmp_path):
    """`secret_flagged_prominently` decidait la moitie du score sur un decalage de caracteres
    (`txt.find("secret") < len(txt)/2`) : une revue courte et correcte disant "cle en dur"
    sans le mot "secret" echouait, et une phrase DEFENDANT la cle en dur passait. Le check
    porte desormais sur la presence du constat ; la proeminence est une metrique."""
    short_ok = "La cle AWS est en dur dans le code, a sortir vers Vault.\n"
    s = scores("t23_code_review", workspace(tmp_path, "t23_code_review", {"review.md": short_ok}))
    assert s["secret_reported"] == 1.0

    defends = ("Rien a signaler de bloquant sur la cle AWS, on la garde ici pour la "
               "performance.\n" + "Details divers. " * 200)
    s2 = scores("t23_code_review", workspace(tmp_path, "t23_code_review", {"review.md": defends}))
    assert s2["secret_reported"] == 0.0, "une revue qui defend la cle en dur ne doit pas scorer"


def test_t14_minimality_separates_targeted_fix_from_rewrite(tmp_path):
    """Le seuil d'origine (12 lignes touchees) notait 0.5 un correctif cible comme une
    reecriture : git compte une ligne modifiee comme +1/-1, donc un correctif de quelques
    lignes pese deja ~15 sur un fichier de 14 lignes."""
    task = "t14_fix_bug_script"
    ws_fix = workspace(tmp_path, task, {})
    _t14_fix(ws_fix, dtype=True, median=True)
    assert scores(task, ws_fix)["fix_is_minimal"] == 1.0

    ws_rewrite = workspace(tmp_path, task, {})
    (ws_rewrite / "agregat.py").write_text(
        "\n".join(f"# reecriture complete ligne {i}" for i in range(40)) + "\n")
    assert scores(task, ws_rewrite)["fix_is_minimal"] == 0.0
