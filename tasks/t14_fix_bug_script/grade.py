"""T14 : corriger un script existant (2 bugs : codes departement passes en numerique -> 2A/2B
perdus et 01 -> 1 ; `mean` la ou on attend `median`). On note le resultat ET la minimalite
du correctif (le prompt demande de ne pas tout reecrire).

Tolerance : comparaison a +/-1 EUR, PAS en relatif. Avec le defaut de 1 %, l'ecart entre la
moyenne et la mediane restait sous la tolerance dans 9 departements sur 13 - le second bug
etait donc invisible et 'ne rien corriger du tout' notait deja 0.54."""
from bench.outcome import (
    Check,
    compare_with_alternatives,
    find,
    git_diff_stat_vs_initial,
    keyed_values,
    load_truth,
    read_table,
    reexecute,
)

T = load_truth(__file__)
OUT = "revenu_median_departement.csv"


def grade(ctx):
    ws = ctx.workspace
    chk, info = reexecute(ws, [OUT])
    rws = info["ws"]
    checks = [chk]
    p = find(rws, OUT)
    got = keyed_values(read_table(p), [r"dep"], [r"median|revenu"]) if p else {}
    checks.append(Check("medians_correct_present", bool(got), 1.0 if got else 0.0,
                        detail=f"{len(got)} ligne(s) lue(s)" if got else f"{OUT} absent"))
    # Reponses fausses NOMMEES : sans elles, un echec se resume a "0/13 ok" et il faut ouvrir
    # le workspace. Sur un run reel, trois cellules avaient invente une ponderation par la
    # population et une avait garde la moyenne d'origine - quatre diagnostics indiscernables.
    checks.append(compare_with_alternatives(
        got, T["median_by_dep"],
        {"moyenne simple, pas une mediane (bug d'origine non corrige)": T["buggy_mean_by_dep"],
         "moyenne ponderee par la population, pas une mediane": T["weighted_mean_by_dep"],
         "mediane ponderee par la population, pas la mediane des communes":
             T["weighted_median_by_dep"]},
        name="medians_correct", rel_tol=0.0, abs_tol=1.0, weight=2.0,
        alt_key=lambda k: k.lstrip("0")))
    kept = [d for d in T["fragile_codes"] if d in got]
    s = len(kept) / len(T["fragile_codes"])
    checks.append(Check("dept_codes_preserved", s >= 0.99, s, detail=f"preserves={kept}"))

    # minimalite du correctif : le prompt dit explicitement "evite de tout reecrire", donc
    # c'est une exigence FONCTIONNELLE de la tache, pas un cout (l'axe efficiency est hors de
    # `combined`, y mettre ce check revenait a le mesurer puis le jeter). Le diff est calcule
    # sur le workspace d'origine, seul a porter l'historique git.
    # `agregat.py` fait 14 lignes ; git compte une ligne MODIFIEE comme un ajout et une
    # suppression, donc un correctif cible de quelques lignes pese deja ~15. Le seuil de 12
    # notait 0.5 aussi bien un correctif cible qu'une reecriture (constate sur un run reel :
    # +11/-6 et +20/-6 recevaient la meme note). Seuils cales sur la taille du fichier : au
    # dela de ~2x ses lignes, c'est une reecriture.
    add, dele = git_diff_stat_vs_initial(ws, "agregat.py")
    touched = add + dele
    ctx.metrics["fix_diff_lines"] = touched
    if touched == 0:
        score, why = 0.0, "agregat.py non modifie"
    elif touched <= 20:
        score, why = 1.0, "correctif cible"
    elif touched <= 30:
        score, why = 0.5, "correctif large"
    else:
        score, why = 0.0, "reecriture"
    checks.append(Check("fix_is_minimal", score >= 0.99, score, weight=0.5,
                        detail=f"+{add}/-{dele} lignes sur agregat.py ({why})"))
    return checks
