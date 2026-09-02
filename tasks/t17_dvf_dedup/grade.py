"""T17 : une mutation DVF = plusieurs lignes (lots, dependances, parcelles). Compter les
lignes surestime de ~40 %. Verite : mutations distinctes (id_mutation), nature 'Vente',
type_local Appartement. Credit 0.5 si le resultat correspond a la version naive (lignes)."""
from bench.outcome import load_truth, reexecute, find, read_table, keyed_values, compare_keyed, Check

T = load_truth(__file__)
OUT = "ventes_appartements_commune.csv"


def grade(ctx):
    ws = ctx.workspace
    checks = [reexecute(ws, [OUT])[0]]
    p = find(ws, OUT)
    if not p:
        return checks + [Check("sales_count_correct", False, 0.0, detail="sortie absente")]
    got = keyed_values(read_table(p), [r"commune|code"], [r"nb|ventes|count|n_"])
    good = compare_keyed(got, T["flat_sales_by_commune"], name="sales_count_correct", abs_tol=0.5)
    naive = compare_keyed(got, T["flat_rows_by_commune_naive"], name="_", abs_tol=0.5)
    if good.score < 0.99 and naive.score > good.score:
        good = Check("sales_count_correct", False, round(0.5 * naive.score, 4),
                     detail=f"compte des lignes, pas des mutations ({naive.detail})")
    ctx.metrics["dedup_exact_frac"] = good.score
    return checks + [good]
