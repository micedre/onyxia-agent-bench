"""T14 : corriger un script existant (2 bugs : codes departement passes en numerique -> 2A/2B
perdus et 01 -> 1 ; `mean` la ou on attend `median`). On note le resultat ET la minimalite
du correctif (le prompt demande de ne pas tout reecrire)."""
from bench.outcome import (load_truth, reexecute, table_check, git_diff_stat_vs_initial,
                           find, read_table, keyed_values, Check)

T = load_truth(__file__)
OUT = "revenu_median_departement.csv"


def grade(ctx):
    ws = ctx.workspace
    checks = [reexecute(ws, [OUT])[0]]
    checks += table_check(ws, OUT, T["median_by_dep"], name="medians_correct",
                          key_patterns=[r"dep"], val_patterns=[r"median|revenu"],
                          alt_key=lambda k: k.lstrip("0"))
    p = find(ws, OUT)
    got = keyed_values(read_table(p), [r"dep"], [r"median|revenu"]) if p else {}
    kept = [d for d in T["fragile_codes"] if d in got]
    s = len(kept) / len(T["fragile_codes"])
    checks.append(Check("dept_codes_preserved", s >= 0.99, s, detail=f"preserves={kept}"))
    add, dele = git_diff_stat_vs_initial(ws, "agregat.py")
    minimal = 0 < add + dele <= 12
    ctx.metrics["fix_diff_lines"] = add + dele
    checks.append(Check("fix_is_minimal", minimal, 1.0 if minimal else (0.5 if add + dele else 0.0),
                        axis="efficiency", detail=f"+{add}/-{dele} lignes sur agregat.py"))
    return checks
