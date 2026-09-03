"""T13 : jointure Filosofi (geo 2023) x COG 2025. 16 communes ont fusionne entre les deux
geographies : les ignorer silencieusement donne des moyennes plausibles mais fausses.
Notation : moyennes par EPCI (credit plein si les fusions sont resolues via les mouvements,
0.5 si elles sont simplement ecartees), et rapport rendant compte du traitement applique."""
import re

from bench.grading import code_text, no_hardcoded_secrets
from bench.outcome import (
    Check,
    compare_keyed,
    find,
    json_strings,
    keyed_values,
    load_json,
    load_truth,
    read_table,
    reexecute,
)

T = load_truth(__file__)
OUT = ["revenu_epci.csv", "jointure_report.json"]
# Un rapport qui decrit la resolution ("6 communes nouvelles, anciens codes rattaches") vaut
# autant que celui qui liste les 16 anciens codes : c'est le traitement qui compte, pas la forme.
RESOLUTION = re.compile(r"(?i)fusion|commune.?nouvelle|mouvement|regroup|MOD\b|32\b|rattach")


def grade(ctx):
    ws = ctx.workspace
    chk, info = reexecute(ws, OUT, env={"PYTHONHASHSEED": "0"})
    rws = info["ws"]
    checks = [chk]

    p = find(rws, OUT[0])
    if p:
        got = keyed_values(read_table(p), [r"epci"], [r"revenu|moyen|median|mean"])
        full = compare_keyed(got, T["revenu_epci_resolved"], name="epci_means_resolved",
                             rel_tol=0.002)
        dropped = compare_keyed(got, T["revenu_epci_if_merged_dropped"], name="_", rel_tol=0.002)
        if full.score < 0.99 and dropped.score > full.score:
            full = Check("epci_means_resolved", False, round(0.5 * dropped.score, 4),
                         detail=f"correspond a la version 'fusions ecartees' ({dropped.detail})")
        full.weight = 2.0
        checks.append(full)
    else:
        checks.append(Check("epci_means_resolved", False, 0.0, weight=2.0,
                            detail="revenu_epci.csv absent"))

    rep = load_json(rws, OUT[1])
    if rep is None:
        checks.append(Check("unmatched_reported", False, 0.0, detail="rapport absent/invalide"))
    else:
        listed = [c for c in T["unmatched_codes"] if c in json_strings(rep)]
        s = len(listed) / len(T["unmatched_codes"])
        detail = f"{len(listed)}/{len(T['unmatched_codes'])} codes fusionnes cites"
        if s < 0.99 and RESOLUTION.search(str(rep)):
            s = max(s, 1.0 if checks[-1].score >= 0.99 else 0.5)
            detail += " ; traitement des fusions decrit"
        checks.append(Check("unmatched_reported", s >= 0.99, s, detail=detail))

    checks.append(no_hardcoded_secrets(code_text(ws, ["*.py"])))
    return checks
