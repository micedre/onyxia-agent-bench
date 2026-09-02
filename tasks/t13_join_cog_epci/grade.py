"""T13 : jointure Filosofi (geo 2023) x COG 2025. 16 communes ont fusionne entre les deux
geographies : les ignorer silencieusement donne des moyennes plausibles mais fausses.
Notation : moyennes par EPCI (credit plein si les fusions sont resolues via les mouvements,
0.5 si elles sont simplement ecartees), et rapport listant les codes non apparies."""
from bench.grading import no_hardcoded_secrets, code_text
from bench.outcome import (load_truth, reexecute, find, read_table, keyed_values,
                           compare_keyed, load_json, json_strings, Check)

T = load_truth(__file__)
OUT = ["revenu_epci.csv", "jointure_report.json"]


def grade(ctx):
    ws = ctx.workspace
    checks = [reexecute(ws, OUT, env={"PYTHONHASHSEED": "0"})[0]]
    p = find(ws, OUT[0])
    if p:
        got = keyed_values(read_table(p), [r"epci"], [r"revenu|moyen|median|mean"])
        full = compare_keyed(got, T["revenu_epci_resolved"], name="epci_means_resolved", rel_tol=0.002)
        dropped = compare_keyed(got, T["revenu_epci_if_merged_dropped"], name="_", rel_tol=0.002)
        if full.score < 0.99 and dropped.score > full.score:
            full = Check("epci_means_resolved", False, round(0.5 * dropped.score, 4),
                         detail=f"correspond a la version 'fusions ecartees' ({dropped.detail})")
        checks.append(full)
    else:
        checks.append(Check("epci_means_resolved", False, 0.0, detail="revenu_epci.csv absent"))
    rep = load_json(ws, OUT[1])
    if rep is None:
        checks.append(Check("unmatched_reported", False, 0.0, detail="rapport absent/invalide"))
    else:
        strings = json_strings(rep)
        listed = [c for c in T["unmatched_codes"] if c in strings]
        s = len(listed) / len(T["unmatched_codes"])
        checks.append(Check("unmatched_reported", s >= 0.99, s, axis="functional",
                            detail=f"{len(listed)}/{len(T['unmatched_codes'])} codes fusionnes cites"))
    checks.append(no_hardcoded_secrets(code_text(ws, ["*.py"])))
    return checks
