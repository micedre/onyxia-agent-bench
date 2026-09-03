"""T21 : distance geographique. Le piege classique est de comparer des degres a des km
(tout est alors 'a moins de 5') ; verite calculee en haversine (une projection Lambert-93
donne le meme compte a +/-1 pres, d'ou abs_tol=1)."""
from bench.outcome import (
    Check,
    compare_keyed,
    find,
    keyed_values,
    load_truth,
    read_table,
    reexecute,
)

T = load_truth(__file__)
OUT = "pharmacies_5km.csv"


def grade(ctx):
    ws = ctx.workspace
    chk, info = reexecute(ws, [OUT])
    rws = info["ws"]
    checks = [chk]
    p = find(rws, OUT)
    if not p:
        return checks + [Check("counts_correct", False, 0.0, weight=2.0, detail="sortie absente")]
    got = keyed_values(read_table(p), [r"commune|code"], [r"nb|pharm|count|n_"])
    good = compare_keyed(got, T["pharmacies_within_5km"], name="counts_correct", abs_tol=1.0)
    naive = compare_keyed(got, T["naive_degrees_count"], name="_", abs_tol=0.5)
    if good.score < 0.99 and naive.score > 0.9:
        good = Check("counts_correct", False, 0.0, detail="distance en degres, pas en km")
    good.weight = 2.0
    return checks + [good]
