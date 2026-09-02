"""Genere un jeu de donnees communal *realiste* (style fichiers INSEE) + sa verite terrain.

Pourquoi : la fixture v0 (6 lignes, un blanc, un -100) ne sollicite aucune competence
reelle. Un vrai fichier INSEE a des pieges que le contexte plateforme est cense aider a
eviter, et qu'un data scientist rencontre des le premier jour :

- separateur `;`, encodage latin-1 (accents dans les noms de communes) ;
- codes departement avec zero en tete (`01`, `06`) et Corse (`2A`, `2B`) -> un parsing
  numerique naif perd le zero ou plante ;
- placeholders de secret statistique (`s`, `nd`) dans des colonnes numeriques ;
- doublons de noms de communes (Saint-Denis, Sainte-Marie...) -> on joint sur le code ;
- valeurs manquantes et anomalies plantees (population 0, coquille x1000, revenu negatif ou
  aberrant), en nombre connu pour pouvoir noter une etape de validation sur son *resultat*.

Deterministe pour une graine donnee. Stdlib uniquement (pas de pandas cote harnais).

    python scripts/gen_insee_fixture.py \
        --out tasks/t02_eda_validation/fixtures/donnees_insee.csv \
        --truth tasks/t02_eda_validation/ground_truth.json --seed 42 --n 1200
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
from pathlib import Path

# (code, effet revenu) : melange de departements "01..09", Corse, grandes villes.
DEPARTEMENTS = {
    "01": 1.02, "02": 0.93, "06": 1.08, "07": 0.95, "09": 0.92, "13": 1.00, "2A": 0.97,
    "2B": 0.94, "31": 1.04, "33": 1.03, "34": 0.96, "35": 1.03, "44": 1.04, "59": 0.92,
    "62": 0.90, "67": 1.05, "69": 1.06, "75": 1.20, "76": 0.98, "78": 1.18, "83": 1.01,
    "92": 1.30, "93": 0.88, "94": 1.05, "95": 1.02,
}
GRANDES_VILLES = {  # commune -> (dep, population) : vrais ordres de grandeur
    "Paris": ("75", 2_145_906), "Marseille": ("13", 873_076), "Lyon": ("69", 522_250),
    "Toulouse": ("31", 498_003), "Nice": ("06", 348_085), "Nantes": ("44", 323_204),
    "Montpellier": ("34", 299_096), "Strasbourg": ("67", 291_313), "Bordeaux": ("33", 260_958),
    "Lille": ("59", 236_234), "Rennes": ("35", 222_485), "Toulon": ("83", 178_745),
    "Le Havre": ("76", 168_290), "Saint-Denis": ("93", 113_942), "Ajaccio": ("2A", 73_487),
    "Bastia": ("2B", 48_500), "Boulogne-Billancourt": ("92", 121_334), "Créteil": ("94", 93_246),
    "Versailles": ("78", 84_808), "Argenteuil": ("95", 110_468),
}
# Noms dupliques a dessein entre departements (le piege "jointure sur le nom").
NOMS_DUPLIQUES = ["Saint-Denis", "Sainte-Marie", "Saint-Pierre", "Saint-Martin", "Villeneuve",
                  "La Chapelle", "Saint-Germain", "Montreuil"]
PREFIXES = ["Saint-", "Sainte-", "Le ", "La ", "Les ", "", "", "", "", ""]
RACINES = ["Aubé", "Bré", "Cha", "Cour", "Dam", "Épin", "Fer", "Gué", "Hé", "Ise", "Jou",
           "Ker", "Lam", "Mé", "Noi", "Ors", "Pré", "Quin", "Rou", "Sé", "Tré", "Vil", "Yz"]
SUFFIXES = ["ville", "court", "ay", "ac", "ey", "ières", "y-sur-Seine", "y-le-Vieux", "ac-sur-Loire",
            "an", "ès", "ac-la-Forêt", "ay-en-Brie", "ol", "ette"]

# Plages "plausibles" utilisees pour la verite terrain (memes seuils que le grader,
# avec une tolerance dans le grader pour des choix raisonnablement differents).
POP_MIN, POP_MAX = 1, 3_000_000
REV_MIN, REV_MAX = 5_000, 100_000


def _nom(rng: random.Random, used: set[str]) -> str:
    for _ in range(50):
        n = rng.choice(PREFIXES) + rng.choice(RACINES) + rng.choice(SUFFIXES)
        if n not in used:
            used.add(n)
            return n
    return f"Commune-{len(used)}"


def generate(n: int, seed: int) -> tuple[list[dict], dict]:
    rng = random.Random(seed)
    deps = list(DEPARTEMENTS)
    rows: list[dict] = []
    used_names: set[str] = set()
    counters = {d: 0 for d in deps}

    def code(dep: str) -> str:
        counters[dep] += 1
        return f"{dep}{counters[dep]:03d}"

    for nom, (dep, pop) in GRANDES_VILLES.items():
        rows.append({"code": code(dep), "nom": nom, "dep": dep, "pop": pop})
        used_names.add(nom)
    for nom in NOMS_DUPLIQUES:  # 2 a 3 communes homonymes dans des departements distincts
        for dep in rng.sample(deps, rng.randint(2, 3)):
            rows.append({"code": code(dep), "nom": nom, "dep": dep,
                         "pop": int(rng.lognormvariate(7.2, 1.1))})
    while len(rows) < n:
        dep = rng.choice(deps)
        rows.append({"code": code(dep), "nom": _nom(rng, used_names), "dep": dep,
                     "pop": max(30, int(rng.lognormvariate(6.6, 1.3)))})

    for r in rows:  # revenu disponible median (euros/an) : effet departement + taille
        base = 21_500 * DEPARTEMENTS[r["dep"]]
        r["rev"] = int(rng.gauss(base + 400 * (r["pop"] > 20_000), 2_300))
        r["rev"] = max(9_000, r["rev"])
    rng.shuffle(rows)

    # --- verite terrain calculee sur les valeurs propres, AVANT salissure ---
    truth_rows = [dict(r) for r in rows]

    # --- salissure : chaque ligne recoit au plus une alteration, comptees exactement ---
    idx = list(range(len(rows)))
    rng.shuffle(idx)
    k = 0

    def take(m: int) -> list[int]:
        nonlocal k
        out = idx[k:k + m]
        k += m
        return out

    n_blank_pop, n_blank_rev = int(n * 0.02), int(n * 0.035)
    n_s_rev, n_nd_rev, n_nd_pop = int(n * 0.012), int(n * 0.005), int(n * 0.004)
    n_pop_zero, n_pop_typo, n_rev_neg, n_rev_huge = 4, 3, 3, 4

    dirty: dict[int, tuple[str, object]] = {}
    for i in take(n_blank_pop):
        dirty[i] = ("pop", "")
    for i in take(n_blank_rev):
        dirty[i] = ("rev", "")
    for i in take(n_s_rev):
        dirty[i] = ("rev", "s")
    for i in take(n_nd_rev):
        dirty[i] = ("rev", "nd")
    for i in take(n_nd_pop):
        dirty[i] = ("pop", "nd")
    for i in take(n_pop_zero):
        dirty[i] = ("pop", 0)
    # coquille "trois zeros de trop" : choisie sur des communes assez grandes pour que la
    # valeur sorte vraiment de la plage plausible (sinon ce n'est pas une anomalie).
    typo_pool = [i for i in idx[k:] if rows[i]["pop"] * 1000 > POP_MAX]
    for i in typo_pool[:n_pop_typo]:
        idx.remove(i)
        dirty[i] = ("pop", rows[i]["pop"] * 1000)
    assert len(typo_pool) >= n_pop_typo
    for i in take(n_rev_neg):
        dirty[i] = ("rev", -rows[i]["rev"])
    for i in take(n_rev_huge):
        dirty[i] = ("rev", rows[i]["rev"] * 100)
    for i, (col, val) in dirty.items():
        rows[i][col] = val
    for i in take(int(n * 0.03)):  # espaces parasites autour du nom (piege de jointure)
        rows[i]["nom"] = " " + rows[i]["nom"] + "  "

    # verite terrain : un agregat exclut les lignes invalides *sur la colonne concernee*
    # seulement (une population manquante ne disqualifie pas un revenu valide, et vice versa).
    valid_rev = [truth_rows[i] for i in range(len(rows)) if dirty.get(i, ("",))[0] != "rev"]
    valid_pop = [truth_rows[i] for i in range(len(rows)) if dirty.get(i, ("",))[0] != "pop"]
    med_by_dep = {}
    for d in deps:
        vals = [r["rev"] for r in valid_rev if r["dep"] == d]
        med_by_dep[d] = statistics.median(vals)
    top10 = sorted(valid_pop, key=lambda r: -r["pop"])[:10]

    truth = {
        "n_rows": len(rows),
        "columns": ["code_commune", "nom_commune", "code_departement", "population",
                    "revenu_disponible_median"],
        "encoding": "latin-1", "sep": ";",
        "missing": {  # blancs stricts ; les placeholders sont comptes a part
            "population": n_blank_pop, "revenu_disponible_median": n_blank_rev},
        "placeholders": {"population": n_nd_pop, "revenu_disponible_median": n_s_rev + n_nd_rev},
        "out_of_range": {"population": n_pop_zero + n_pop_typo,
                         "revenu_disponible_median": n_rev_neg + n_rev_huge},
        "ranges": {"population": [POP_MIN, POP_MAX],
                   "revenu_disponible_median": [REV_MIN, REV_MAX]},
        "n_departements": len(deps),
        "departements_zero_en_tete": [d for d in deps if d[0] == "0"],
        "departements_corse": ["2A", "2B"],
        "duplicate_names": NOMS_DUPLIQUES,
        "median_revenu_by_departement": med_by_dep,
        "top10_population": [{"code_commune": r["code"], "nom_commune": r["nom"],
                              "population": r["pop"]} for r in top10],
        "seed": seed,
    }
    return rows, truth


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["code_commune;nom_commune;code_departement;population;revenu_disponible_median"]
    for r in rows:
        lines.append(f"{r['code']};{r['nom']};{r['dep']};{r['pop']};{r['rev']}")
    path.write_bytes(("\n".join(lines) + "\n").encode("latin-1"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--truth", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n", type=int, default=1200)
    a = ap.parse_args()
    rows, truth = generate(a.n, a.seed)
    write_csv(rows, a.out)
    a.truth.parent.mkdir(parents=True, exist_ok=True)
    a.truth.write_text(json.dumps(truth, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{len(rows)} lignes -> {a.out} ; verite terrain -> {a.truth}")


if __name__ == "__main__":
    main()
