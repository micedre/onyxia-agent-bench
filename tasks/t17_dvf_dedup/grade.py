"""T17 : une mutation DVF = plusieurs lignes (lots, dependances, parcelles). Compter les
lignes surestime de ~40 %. Verite : mutations distinctes (id_mutation), nature 'Vente',
type_local Appartement. Credit 0.5 si le resultat correspond a la version naive (lignes)."""
from bench.outcome import (
    Check,
    compare_with_alternatives,
    find_output,
    keyed_values,
    load_truth,
    read_table,
    reexecute,
)

T = load_truth(__file__)
OUT = "ventes_appartements_commune.csv"


def grade(ctx):
    ws = ctx.workspace
    chk, info = reexecute(ws, [OUT])
    rws = info["ws"]
    checks = [chk]
    p = find_output(rws, OUT)
    if not p:
        return checks + [Check("sales_count_correct", False, 0.0, weight=2.0,
                               detail="sortie absente")]
    got = keyed_values(read_table(p), [r"commune|code"], [r"nb|ventes|count|n_"])
    good = compare_with_alternatives(
        got, T["flat_sales_by_commune"],
        {"compte des lignes, pas des mutations": T["flat_rows_by_commune_naive"]},
        name="sales_count_correct", abs_tol=0.5, weight=2.0)
    ctx.metrics["dedup_exact_frac"] = good.score
    return checks + [good]
