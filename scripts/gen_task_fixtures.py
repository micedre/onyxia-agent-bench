"""Genere les fixtures synthetiques (schemas des vraies sources : Filosofi, COG, DVF
geolocalise, BPE, fichier detail RP) et leur verite terrain pour les tasks t13..t24.

Les vraies donnees (data.gouv.fr / insee.fr) ne sont pas commitees : trop volumineuses ou
sans verite terrain calculable a l'avance. Chaque fixture ici reproduit le schema, les
pieges et les ordres de grandeur du vrai fichier, en taille git-compatible, et la verite
terrain est derivee du meme tirage. Pour substituer un vrai extrait, voir README (section
"Fixtures reelles").

    python scripts/gen_task_fixtures.py [--seed 42] [--tasks t13,t17] [--tasks-dir tasks]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "tasks"

REGIONS = {  # dep -> (code region, libelle)
    "01": ("84", "Auvergne-Rhone-Alpes"), "06": ("93", "Provence-Alpes-Cote d'Azur"),
    "13": ("93", "Provence-Alpes-Cote d'Azur"), "2A": ("94", "Corse"), "2B": ("94", "Corse"),
    "31": ("76", "Occitanie"), "33": ("75", "Nouvelle-Aquitaine"), "35": ("53", "Bretagne"),
    "44": ("52", "Pays de la Loire"), "59": ("32", "Hauts-de-France"),
    "69": ("84", "Auvergne-Rhone-Alpes"), "75": ("11", "Ile-de-France"),
    "93": ("11", "Ile-de-France"),
}
RACINES = ["Aubé", "Bré", "Cha", "Cour", "Dam", "Épin", "Fer", "Gué", "Hé", "Ise", "Jou", "Ker",
           "Lam", "Mé", "Noi", "Ors", "Pré", "Quin", "Rou", "Sé", "Tré", "Vil", "Yz", "Bou", "Cas"]
SUFFIXES = ["ville", "court", "ay", "ac", "ey", "ières", "y-sur-Seine", "y-le-Vieux", "an", "ès",
            "ac-la-Forêt", "ol", "ette", "zac", "gnan"]
PREFIXES = ["Saint-", "Sainte-", "Le ", "La ", "Les ", "", "", "", "", ""]
HOMONYMES = ["Saint-Denis", "Sainte-Marie", "Saint-Pierre", "Saint-Martin", "Villeneuve"]


# --------------------------------------------------------------------------- univers communal


def communes(rng: random.Random, n: int = 1500) -> list[dict]:
    """Communes avec code, nom, dep, region, EPCI, population, nb menages, revenu."""
    deps = list(REGIONS)
    out, used, counters = [], set(), {d: 0 for d in deps}
    epci_by_dep = {d: [f"2{rng.randint(10_000_000, 99_999_999)}" for _ in range(rng.randint(3, 6))]
                   for d in deps}

    def new(dep: str, nom: str, pop: int) -> dict:
        counters[dep] += 1
        eff = {"75": 1.20, "92": 1.3, "93": 0.88, "06": 1.08, "69": 1.06, "2A": 0.97,
               "2B": 0.94, "59": 0.92}.get(dep, 1.0)
        rev = max(9_000, int(rng.gauss(21_500 * eff + 400 * (pop > 20_000), 2_300)))
        return {"code": f"{dep}{counters[dep]:03d}", "nom": nom, "dep": dep,
                "reg": REGIONS[dep][0], "epci": rng.choice(epci_by_dep[dep]), "pop": pop,
                "menages": max(10, int(pop / rng.uniform(2.0, 2.4))), "rev": rev}

    for nom, dep, pop in [("Paris", "75", 2_145_906), ("Marseille", "13", 873_076),
                          ("Lyon", "69", 522_250), ("Toulouse", "31", 498_003),
                          ("Nice", "06", 348_085), ("Nantes", "44", 323_204),
                          ("Bordeaux", "33", 260_958), ("Lille", "59", 236_234),
                          ("Rennes", "35", 222_485), ("Ajaccio", "2A", 73_487),
                          ("Bastia", "2B", 48_500), ("Saint-Denis", "93", 113_942)]:
        out.append(new(dep, nom, pop))
        used.add(nom)
    for nom in HOMONYMES:
        for dep in rng.sample(deps, rng.randint(2, 3)):
            out.append(new(dep, nom, int(rng.lognormvariate(7.2, 1.1))))
    while len(out) < n:
        dep = rng.choice(deps)
        for _ in range(50):
            nom = rng.choice(PREFIXES) + rng.choice(RACINES) + rng.choice(SUFFIXES)
            if nom not in used:
                used.add(nom)
                break
        out.append(new(dep, nom, max(30, int(rng.lognormvariate(6.6, 1.3)))))
    rng.shuffle(out)
    return out


def write_csv(path: Path, header: list[str], rows: list[list], *, sep=",", enc="utf-8"):
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = [sep.join(header)] + [sep.join("" if v is None else str(v) for v in r) for r in rows]
    path.write_bytes(("\n".join(buf) + "\n").encode(enc))


def write_truth(task: str, truth: dict):
    (TASKS / task / "ground_truth.json").write_text(
        json.dumps(truth, indent=2, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------- t13 : jointure COG/EPCI


def gen_t13(rng: random.Random):
    task = "t13_join_cog_epci"
    fx = TASKS / task / "fixtures"
    com = communes(rng, 1500)
    # Filosofi 2021 en geographie 2023 ; COG 2025 : des communes ont fusionne entre-temps.
    fusions = []  # (anciens codes, nouveau code, nouveau nom)
    pool = [c for c in com if c["pop"] < 5_000]
    rng.shuffle(pool)
    for i in range(6):
        k = rng.randint(2, 3)
        olds = pool[i * 3:i * 3 + k]
        if len(olds) < 2:
            continue
        dep = olds[0]["dep"]
        for o in olds:  # les membres d'une commune nouvelle sont dans le meme departement
            o["dep"] = dep
            o["reg"] = REGIONS[dep][0]
            # rurales : plus pauvres et non negligeables dans leur EPCI -> les ecarter
            # silencieusement deplace la moyenne de facon mesurable
            o["rev"] = int(o["rev"] * 0.72)
            o["menages"] = int(o["menages"] * 6) + 800
        new_code = f"{dep}{900 + i:03d}"
        fusions.append({"olds": [o["code"] for o in olds], "new": new_code,
                        "nom": olds[0]["nom"] + "-en-Val", "epci": olds[0]["epci"]})
    old_to_new = {o: f["new"] for f in fusions for o in f["olds"]}
    renamed = rng.sample([c for c in com if c["code"] not in old_to_new], 3)

    # --- filosofi_communes_2021.csv (geographie 2023) : `;`, quelques `s` (secret stat.)
    secret = set(c["code"] for c in rng.sample(com, int(len(com) * 0.02)))
    rows = [[c["code"], c["nom"], c["menages"], "s" if c["code"] in secret else c["rev"]]
            for c in com]
    write_csv(fx / "filosofi_communes_2021.csv",
              ["CODGEO", "LIBGEO", "NBMENFISC21", "MED21"], rows, sep=";")

    # --- cog_communes_2025.csv : les anciennes disparaissent, les nouvelles apparaissent
    cog_rows = [[c["code"], c["nom"] if c not in renamed else c["nom"] + "-le-Neuf",
                 c["dep"], c["reg"], c["epci"]] for c in com if c["code"] not in old_to_new]
    for f in fusions:
        dep = f["new"][:2]
        cog_rows.append([f["new"], f["nom"], dep, REGIONS[dep][0], f["epci"]])
    cog_rows.sort()
    write_csv(fx / "cog_communes_2025.csv", ["COM", "LIBELLE", "DEP", "REG", "EPCI"], cog_rows)

    # --- cog_mouvements_2024_2025.csv (format Insee : mod 32 = commune nouvelle, 10 = nom)
    mv = []
    by_code = {c["code"]: c for c in com}
    for f in fusions:
        for o in f["olds"]:
            mv.append([32, "2025-01-01", o, by_code[o]["nom"], f["new"], f["nom"]])
    for c in renamed:
        mv.append([10, "2025-01-01", c["code"], c["nom"], c["code"], c["nom"] + "-le-Neuf"])
    write_csv(fx / "cog_mouvements_2024_2025.csv",
              ["MOD", "DATE_EFF", "COM_AV", "LIBELLE_AV", "COM_AP", "LIBELLE_AP"], mv)

    # --- verite terrain
    def epci_means(resolve: bool) -> dict[str, float]:
        acc: dict[str, list] = {}
        for c in com:
            if c["code"] in secret:
                continue
            code = c["code"]
            if code in old_to_new:
                if not resolve:
                    continue
                epci = next(f["epci"] for f in fusions if f["new"] == old_to_new[code])
            else:
                epci = c["epci"]
            acc.setdefault(epci, []).append((c["menages"], c["rev"]))
        return {e: round(sum(w * r for w, r in v) / sum(w for w, _ in v), 2)
                for e, v in acc.items()}

    write_truth(task, {
        "revenu_epci_resolved": epci_means(True),
        "revenu_epci_if_merged_dropped": epci_means(False),
        "unmatched_codes": sorted(old_to_new),
        "n_unmatched": len(old_to_new),
        "n_secret": len(secret), "fusions": fusions,
    })


# --------------------------------------------------------------------------- t14 : bug fix


def gen_t14(rng: random.Random):
    task = "t14_fix_bug_script"
    fx = TASKS / task / "fixtures"
    com = communes(rng, 900)
    write_csv(fx / "donnees_communes.csv", ["code_commune", "nom_commune", "code_departement",
                                            "population", "revenu_disponible_median"],
              [[c["code"], c["nom"], c["dep"], c["pop"], c["rev"]] for c in com], sep=";")
    (fx / "agregat.py").write_text('''"""Revenu disponible median par departement (script hebdo, tourne depuis 2023)."""
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
''', encoding="utf-8")
    by_dep: dict[str, list] = {}
    pairs_by_dep: dict[str, list] = {}
    for c in com:
        by_dep.setdefault(c["dep"], []).append(c["rev"])
        pairs_by_dep.setdefault(c["dep"], []).append((c["rev"], c["pop"]))

    def weighted_mean(pairs):
        tot = sum(w for _, w in pairs)
        return round(sum(v * w for v, w in pairs) / tot, 1) if tot else None

    def weighted_median(pairs):
        """Mediane ponderee : premiere valeur dont le cumul des poids atteint la moitie."""
        ordered = sorted(pairs)
        half = sum(w for _, w in ordered) / 2
        cum = 0.0
        for v, w in ordered:
            cum += w
            if cum >= half:
                return float(v)
        return None

    # `buggy_*` et `weighted_*` ne sont pas la verite : ce sont des reponses FAUSSES nommees,
    # que le grader compare pour dire *pourquoi* une cellule echoue au lieu d'afficher
    # "0/13 ok". Constate sur un run reel : trois cellules avaient invente une ponderation par
    # la population (moyenne ou mediane) et une avait garde la moyenne d'origine - quatre
    # diagnostics impossibles a distinguer sans ouvrir les workspaces.
    write_truth(task, {"median_by_dep": {d: statistics.median(v) for d, v in by_dep.items()},
                       "buggy_mean_by_dep": {d: round(statistics.mean(v)) for d, v in by_dep.items()},
                       "weighted_mean_by_dep": {d: weighted_mean(v) for d, v in pairs_by_dep.items()},
                       "weighted_median_by_dep": {d: weighted_median(v) for d, v in pairs_by_dep.items()},
                       "fragile_codes": [d for d in by_dep if d[0] == "0" or d in ("2A", "2B")],
                       "n_communes": len(com)})


# --------------------------------------------------------------------------- t15 : RP hors memoire


def gen_t15(rng: random.Random, n: int = 300_000):
    task = "t15_outofcore_census"
    fx = TASKS / task / "fixtures"
    fx.mkdir(parents=True, exist_ok=True)
    deps = list(REGIONS)
    dep_w = [rng.uniform(0.3, 3.0) for _ in deps]
    cols = {k: [] for k in ("CANTVILLE", "DEPT", "REGION", "IPONDI", "AGED", "SEXE", "STOCD",
                            "TACT", "DIPL", "CS1", "TYPMR", "NPERR", "SURF", "ANEMR", "IMMI",
                            "INATC", "LPRM", "MOCO", "TP", "VOIT")}
    for _ in range(n):
        dep = rng.choices(deps, dep_w)[0]
        age = min(105, max(0, int(rng.gauss(41 + (3 if dep in ("2A", "2B", "06") else 0), 23))))
        stocd = rng.choices(["10", "21", "22", "23", "30"],
                            [0.58 if dep not in ("75", "93") else 0.33, 0.25, 0.08, 0.04, 0.05])[0]
        cols["CANTVILLE"].append(dep + str(rng.randint(1, 30)).zfill(2))
        cols["DEPT"].append(dep)
        cols["REGION"].append(REGIONS[dep][0])
        cols["IPONDI"].append(round(rng.uniform(1.5, 8.0), 4))
        cols["AGED"].append(str(age).zfill(3))
        cols["SEXE"].append(rng.choice(["1", "2"]))
        cols["STOCD"].append(stocd)
        cols["TACT"].append(rng.choice(["11", "12", "21", "22", "23", "24"]))
        cols["DIPL"].append(rng.choice(["01", "02", "03", "11", "12", "13", "14", "15"]))
        cols["CS1"].append(rng.choice(["1", "2", "3", "4", "5", "6", "7", "8"]))
        cols["TYPMR"].append(rng.choice(["11", "12", "21", "22"]))
        cols["NPERR"].append(rng.choice(["1", "2", "3", "4", "5"]))
        cols["SURF"].append(rng.choice(["1", "2", "3", "4", "5", "6", "7"]))
        cols["ANEMR"].append(rng.choice(["1", "2", "3", "4", "5", "6"]))
        cols["IMMI"].append(rng.choice(["1", "2"]))
        cols["INATC"].append(rng.choice(["1", "2"]))
        cols["LPRM"].append(rng.choice(["1", "2", "3", "4"]))
        cols["MOCO"].append(rng.choice(["11", "12", "21", "22", "23", "31", "32"]))
        cols["TP"].append(rng.choice(["1", "2", "Z"]))
        cols["VOIT"].append(rng.choice(["0", "1", "2", "3"]))
    table = pa.table({k: pa.array(v, type=pa.float64() if k == "IPONDI" else pa.string())
                      for k, v in cols.items()})
    pq.write_table(table, fx / "fd_indcvi_2020_sample.parquet", compression="zstd",
                   row_group_size=50_000)

    # verite terrain : par DEPT, age median pondere (IPONDI) et part de locataires (STOCD 2x)
    truth_age, truth_loc = {}, {}
    for dep in deps:
        idx = [i for i in range(n) if cols["DEPT"][i] == dep]
        pairs = sorted((int(cols["AGED"][i]), cols["IPONDI"][i]) for i in idx)
        total = sum(w for _, w in pairs)
        cum = 0.0
        for a, w in pairs:
            cum += w
            if cum >= total / 2:
                truth_age[dep] = a
                break
        truth_loc[dep] = round(sum(cols["IPONDI"][i] for i in idx
                                   if cols["STOCD"][i].startswith("2")) / total, 4)
    write_truth(task, {"n_rows": n, "weighted_median_age_by_dept": truth_age,
                       "tenant_share_by_dept": truth_loc,
                       "file_mb": round((fx / "fd_indcvi_2020_sample.parquet").stat().st_size / 1e6, 2)})


# --------------------------------------------------------------------------- t16/t17 : DVF


def gen_dvf(rng: random.Random, task: str, n_mutations: int = 6000) -> dict:
    fx = TASKS / task / "fixtures"
    com = [c for c in communes(rng, 400) if c["dep"] in ("33", "69")]
    if not com:
        raise RuntimeError("pas de communes 33/69")
    coeff = {c["code"]: rng.uniform(1_800, 5_500) for c in com}  # €/m² communal
    sigma = 350.0  # bruit irreductible €/m²
    header = ["id_mutation", "date_mutation", "numero_disposition", "nature_mutation",
              "valeur_fonciere", "code_commune", "nom_commune", "code_departement",
              "id_parcelle", "nombre_lots", "code_type_local", "type_local",
              "surface_reelle_bati", "nombre_pieces_principales", "longitude", "latitude"]
    rows, n_flat_sales, n_flat_rows = [], {}, {}
    for i in range(n_mutations):
        c = rng.choice(com)
        nature = rng.choices(["Vente", "Vente en l'état futur d'achèvement", "Echange",
                              "Adjudication"], [0.9, 0.05, 0.03, 0.02])[0]
        kind = rng.choices(["appart", "maison", "terrain"], [0.55, 0.3, 0.15])[0]
        date = f"2023-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        mid = f"2023-{i + 1:06d}"
        lon = 4.83 + rng.gauss(0, 0.08) if c["dep"] == "69" else -0.58 + rng.gauss(0, 0.08)
        lat = 45.75 + rng.gauss(0, 0.06) if c["dep"] == "69" else 44.84 + rng.gauss(0, 0.06)
        if kind == "terrain":
            rows.append([mid, date, 1, nature, rng.randint(20_000, 300_000), c["code"], c["nom"],
                         c["dep"], f"{c['code']}000AB{rng.randint(1, 999):04d}", 0, "", "",
                         "", "", round(lon, 6), round(lat, 6)])
            continue
        surf = int(rng.lognormvariate(4.1 if kind == "appart" else 4.7, 0.4))
        pieces = max(1, min(8, round(surf / 22 + rng.gauss(0, 0.7))))
        pm2 = coeff[c["code"]] * (1.15 if kind == "maison" else 1.0) \
            * (1 - 0.004 * max(0, surf - 60)) + rng.gauss(0, sigma)
        price = max(15_000, int(pm2 * surf))
        tl = ("2", "Appartement") if kind == "appart" else ("1", "Maison")
        rows.append([mid, date, 1, nature, price, c["code"], c["nom"], c["dep"],
                     f"{c['code']}000AB{rng.randint(1, 999):04d}", 1 if kind == "appart" else 0,
                     tl[0], tl[1], surf, pieces, round(lon, 6), round(lat, 6)])
        extra = rng.choices([0, 1, 2, 3], [0.55, 0.3, 0.1, 0.05])[0]
        dup_same_type = 0
        for _ in range(extra):  # MEME id_mutation et prix : dependance (cave, parking) ou la
            # meme ligne repetee sur une autre parcelle / un autre lot (cas frequent dans DVF)
            if rng.random() < 0.5:
                rows.append([mid, date, 1, nature, price, c["code"], c["nom"], c["dep"],
                             f"{c['code']}000AB{rng.randint(1, 999):04d}", 1, "3", "Dépendance",
                             rng.choice(["", "12", "18"]), 0, round(lon, 6), round(lat, 6)])
            else:
                dup_same_type += 1
                rows.append([mid, date, 1, nature, price, c["code"], c["nom"], c["dep"],
                             f"{c['code']}000AB{rng.randint(1, 999):04d}", 2, tl[0], tl[1],
                             surf, pieces, round(lon, 6), round(lat, 6)])
        if kind == "appart" and nature == "Vente":
            n_flat_sales[c["code"]] = n_flat_sales.get(c["code"], 0) + 1
            n_flat_rows[c["code"]] = n_flat_rows.get(c["code"], 0) + 1 + dup_same_type
    write_csv(fx / "dvf_2023_dep33_69.csv", header, rows)
    flats = [r for r in rows if r[11] == "Appartement" and r[3] == "Vente"]
    pm2s = [r[4] / r[12] for r in flats]
    return {"n_rows": len(rows), "n_mutations": n_mutations,
            "flat_sales_by_commune": n_flat_sales, "flat_rows_by_commune_naive": n_flat_rows,
            "noise_sigma_eur_m2": sigma,
            "baseline_rmse_eur_m2": round(statistics.pstdev(pm2s), 1),
            "n_flats": len(flats)}


def gen_t16(rng: random.Random):
    write_truth("t16_dvf_price_model", gen_dvf(rng, "t16_dvf_price_model"))


def gen_t17(rng: random.Random):
    write_truth("t17_dvf_dedup", gen_dvf(rng, "t17_dvf_dedup"))


# --------------------------------------------------------------------------- t18 : notebook


FAKE_TOKEN = "eyJhbGciOiJIUzI1NiJ9.FIXTURE-SESSION-TOKEN-7f3a9c2e.do-not-commit"


def gen_t18(rng: random.Random):
    task = "t18_notebook_refactor"
    fx = TASKS / task / "fixtures"
    com = communes(rng, 700)
    write_csv(fx / "donnees_communes.csv", ["code_commune", "nom_commune", "code_departement",
                                            "population", "revenu_disponible_median"],
              [[c["code"], c["nom"], c["dep"], c["pop"], c["rev"]] for c in com], sep=";")
    seuil = 19_500
    by_dep: dict[str, list] = {}
    for c in com:
        by_dep.setdefault(c["dep"], []).append(c["rev"] < seuil)
    truth = {d: round(sum(v) / len(v), 4) for d, v in by_dep.items()}

    def cell(src, outputs=None, count=None, kind="code"):
        c = {"cell_type": kind, "metadata": {}, "source": src}
        if kind == "code":
            c["execution_count"] = count
            c["outputs"] = outputs or []
        return c

    def stream(text):
        return [{"output_type": "stream", "name": "stdout", "text": text}]

    # Cellules dans un ordre qui ne s'execute PAS de haut en bas (execution_count le montre),
    # `seuil` redefini plus bas apres usage, token de session affiche dans une sortie.
    nb = {"nbformat": 4, "nbformat_minor": 5, "metadata": {"kernelspec": {"name": "python3",
          "display_name": "Python 3", "language": "python"}}, "cells": [
        cell("# Part des communes sous le seuil de bas revenu, par departement", kind="markdown"),
        cell("import os, pandas as pd\nprint('token S3 :', os.environ.get('AWS_SESSION_TOKEN'))",
             stream(f"token S3 : {FAKE_TOKEN}\n"), 1),
        cell("res = (df.assign(sous_seuil=df.revenu_disponible_median < seuil)\n"
             "        .groupby('code_departement')['sous_seuil'].mean().round(4))\nres",
             [{"output_type": "execute_result", "execution_count": 7, "metadata": {},
               "data": {"text/plain": "code_departement\n01    0.4321\n...\n"}}], 7),
        cell("df = pd.read_csv('donnees_communes.csv', sep=';', dtype={'code_departement': str})\n"
             "df.head()", [], 3),
        cell("seuil = 21000  # ancien seuil, ne pas utiliser", [], 4),
        cell("seuil = 19500", [], 6),
        cell("res.to_csv('part_communes_sous_seuil.csv', header=['part_sous_seuil'])", [], 8),
    ]}
    untracked = TASKS / task / "fixtures_untracked"
    untracked.mkdir(parents=True, exist_ok=True)
    (untracked / "analyse_bas_revenus.ipynb").write_text(json.dumps(nb, indent=1, ensure_ascii=False),
                                                  encoding="utf-8")
    write_truth(task, {"seuil": seuil, "share_below_by_dep": truth, "token": FAKE_TOKEN})


# --------------------------------------------------------------------------- t19 : publication


def gen_t19(rng: random.Random):
    task = "t19_publish_diffusion"
    fx = TASKS / task / "fixtures"
    com = communes(rng, 800)
    acc: dict[str, list] = {}
    for c in com:
        acc.setdefault(c["epci"], []).append((c["menages"], c["rev"]))
    rows = [[e, round(sum(w * r for w, r in v) / sum(w for w, _ in v), 2), sum(w for w, _ in v),
             len(v)] for e, v in sorted(acc.items())]
    write_csv(fx / "revenu_epci.csv", ["code_epci", "revenu_moyen_pondere", "nb_menages",
                                        "nb_communes"], rows)
    write_truth(task, {"columns": ["code_epci", "revenu_moyen_pondere", "nb_menages",
                                   "nb_communes"], "n_rows": len(rows),
                       "source": "Filosofi 2021", "cog_vintage": "2025"})


# --------------------------------------------------------------------------- t20 : quarto


def gen_t20(rng: random.Random):
    task = "t20_quarto_param"
    fx = TASKS / task / "fixtures"
    com = communes(rng, 1000)
    write_csv(fx / "donnees_communes.csv",
              ["code_commune", "nom_commune", "code_departement", "code_region",
               "population", "revenu_disponible_median"],
              [[c["code"], c["nom"], c["dep"], c["reg"], c["pop"], c["rev"]] for c in com],
              sep=";")
    acc: dict[str, list] = {}
    for c in com:
        acc.setdefault(c["reg"], []).append(c)
    truth = {r: {"n_communes": len(v), "population": sum(c["pop"] for c in v),
                 "revenu_median": statistics.median(c["rev"] for c in v)} for r, v in acc.items()}
    write_truth(task, {"by_region": truth, "regions": sorted(acc)})


# --------------------------------------------------------------------------- t21 : geo BPE


def _haversine_km(lon1, lat1, lon2, lat2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


def gen_t21(rng: random.Random):
    task = "t21_geo_bpe"
    fx = TASKS / task / "fixtures"
    com = [c for c in communes(rng, 900) if c["dep"] == "33"]
    centro = {c["code"]: (round(-0.58 + rng.gauss(0, 0.35), 6), round(44.84 + rng.gauss(0, 0.25), 6))
              for c in com}
    write_csv(fx / "communes_centroides_dep33.csv", ["code_commune", "nom_commune", "longitude",
                                                     "latitude"],
              [[c["code"], c["nom"], *centro[c["code"]]] for c in com])
    types = [("D301", "Pharmacie", 0.25), ("A203", "Banque", 0.15), ("B203", "Boulangerie", 0.3),
             ("D201", "Medecin generaliste", 0.2), ("C104", "Ecole elementaire", 0.1)]
    bpe = []
    for c in com:
        lon0, lat0 = centro[c["code"]]
        n_eq = max(1, int(math.log1p(c["pop"]) * 1.5))
        for _ in range(n_eq):
            t = rng.choices(types, [w for _, _, w in types])[0]
            bpe.append([f"{c['code']}{rng.randint(1, 9999):04d}", c["code"], t[0],
                        round(lon0 + rng.gauss(0, 0.02), 6), round(lat0 + rng.gauss(0, 0.015), 6)])
    rng.shuffle(bpe)
    write_csv(fx / "bpe23_dep33.csv", ["ID", "DEPCOM", "TYPEQU", "LONGITUDE", "LATITUDE"], bpe)
    pharm = [(r[3], r[4]) for r in bpe if r[2] == "D301"]
    truth, naive = {}, {}
    for c in com:
        lon0, lat0 = centro[c["code"]]
        truth[c["code"]] = sum(1 for lon, lat in pharm if _haversine_km(lon0, lat0, lon, lat) <= 5)
        naive[c["code"]] = sum(1 for lon, lat in pharm if math.hypot(lon - lon0, lat - lat0) <= 5)
    write_truth(task, {"pharmacies_within_5km": truth, "naive_degrees_count": naive,
                       "n_pharmacies": len(pharm), "typequ_pharmacie": "D301"})


# --------------------------------------------------------------------------- t23 : code review


def gen_t23(rng: random.Random):
    task = "t23_code_review"
    fx = TASKS / task / "fixtures"
    (fx / "src").mkdir(parents=True, exist_ok=True)
    (fx / "src" / "pipeline.py").write_text('''"""Pipeline hebdo : revenu par EPCI a partir du RP et de Filosofi."""
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.linear_model import Ridge

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
''', encoding="utf-8")
    (fx / "PR.md").write_text("# PR #42 : pipeline revenu par EPCI\n\nAjoute `src/pipeline.py` "
                              "(chargement RP + Filosofi, jointure, modele). Merci de relire.\n",
                              encoding="utf-8")
    write_truth(task, {"issues": {
        "hardcoded_secret": "cle AWS en dur dans le code (et transmise en storage_options)",
        "full_file_in_memory": "read_parquet du fichier RP national complet, aucune colonne/filtre",
        "join_on_name": "jointure sur le libelle de commune (homonymes) au lieu du code",
        "no_seed": "train_test_split sans random_state / aucune graine",
        "no_holdout_logging": "score imprime, rien de logue (MLflow) ni de persiste",
    }})


# --------------------------------------------------------------------------- t24 : API mock


def gen_t24(rng: random.Random):
    task = "t24_api_ingestion"
    fx = TASKS / task / "fixtures"
    fx.mkdir(parents=True, exist_ok=True)
    n = 1270
    (fx / "mock_stats_api.py").write_text('''"""Mock local d'une API de series statistiques (style SDMX-JSON simplifie).

    python mock_stats_api.py [port]      # defaut 8765

GET /series/pop_dep?page=N&size=200  -> {"data": [...], "page": N, "total": T, "next": url|null}
Chaque reponse porte un ETag ; un GET avec If-None-Match identique renvoie 304.
Chaque requete est journalisee (une ligne JSON) dans requests.log a cote du script.
"""
import hashlib
import json
import random
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

LOG = Path(__file__).with_name("requests.log")
N = 1270
rng = random.Random(7)
DEPS = ["01", "06", "13", "2A", "2B", "31", "33", "35", "44", "59", "69", "75", "93"]
ROWS = [{"dep": DEPS[i % len(DEPS)], "period": f"{2000 + i // len(DEPS)}",
         "value": rng.randint(100_000, 2_500_000)} for i in range(N)]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        with LOG.open("a") as f:
            f.write(json.dumps({"path": self.path,
                                "if_none_match": self.headers.get("If-None-Match")}) + "\\n")
        if u.path != "/series/pop_dep":
            self.send_response(404); self.end_headers(); return
        page, size = int(q.get("page", ["1"])[0]), min(200, int(q.get("size", ["100"])[0]))
        chunk = ROWS[(page - 1) * size: page * size]
        nxt = f"/series/pop_dep?page={page + 1}&size={size}" if page * size < N else None
        body = json.dumps({"data": chunk, "page": page, "total": N, "next": nxt}).encode()
        etag = hashlib.md5(body).hexdigest()
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    HTTPServer(("127.0.0.1", port), H).serve_forever()
''', encoding="utf-8")
    (fx / "API.md").write_text("Voir `mock_stats_api.py` (docstring). URL de base dans "
                               "`$STATS_API_URL` (ex. `http://127.0.0.1:8765`).\n", encoding="utf-8")
    write_truth(task, {"n_rows": n, "page_size_max": 200, "n_pages_min": math.ceil(n / 200)})


# --------------------------------------------------------------------------- main

GENERATORS = {"t13": gen_t13, "t14": gen_t14, "t15": gen_t15, "t16": gen_t16, "t17": gen_t17,
              "t18": gen_t18, "t19": gen_t19, "t20": gen_t20, "t21": gen_t21, "t23": gen_t23,
              "t24": gen_t24}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--tasks", default=",".join(GENERATORS))
    a = ap.parse_args()
    for t in a.tasks.split(","):
        GENERATORS[t](random.Random(f"{a.seed}-{t}"))
        print("ok", t)


if __name__ == "__main__":
    main()
