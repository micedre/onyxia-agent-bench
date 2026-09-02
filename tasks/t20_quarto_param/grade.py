"""T20 : rapport Quarto parametre. Resultat (CSV regional vs verite) + `.qmd` avec bloc
`params:` dans le frontmatter + un mecanisme de rendu par region (script/Makefile bouclant
sur les regions ou `quarto render -P`) + rendu best-effort si quarto est installe."""
import re
from bench.grading import file_exists, best_effort_render, code_text
from bench.outcome import load_truth, reexecute, find, read_table, to_float, compare_keyed, Check

T = load_truth(__file__)
OUT = "indicateurs_regions.csv"


def grade(ctx):
    ws = ctx.workspace
    checks = [reexecute(ws, [OUT])[0]]
    p = find(ws, OUT)
    if p:
        rows = read_table(p)
        cols = list(rows[0].keys()) if rows else []
        rc = next((c for c in cols if re.search(r"(?i)reg", c)), None)
        for key, pats, tol in (("n_communes", r"n_?communes|nb", 0.5), ("population", r"pop", 0.5),
                               ("revenu_median", r"revenu|median", 1.0)):
            vc = next((c for c in cols if re.search(pats, c, re.I) and c != rc), None)
            got = {str(r[rc]).strip(): to_float(r.get(vc)) for r in rows} if rc and vc else {}
            truth = {r: v[key] for r, v in T["by_region"].items()}
            checks.append(compare_keyed({k: v for k, v in got.items() if v is not None}, truth,
                                        name=f"{key}_correct", abs_tol=tol))
    else:
        checks.append(Check("indicators_correct", False, 0.0, detail="CSV absent"))
    qmd = find(ws, "*.qmd")
    checks.append(file_exists(ws, ["*.qmd"], name="qmd_present"))
    txt = qmd.read_text(encoding="utf-8", errors="replace") if qmd else ""
    fm = txt.split("---")[1] if txt.startswith("---") and txt.count("---") >= 2 else ""
    param = bool(re.search(r"^\s*params\s*:", fm, re.M))
    checks.append(Check("qmd_parameterized", param, 1.0 if param else 0.0, axis="repro",
                        detail="params: dans le frontmatter" if param else "pas de params:"))
    driver = code_text(ws, ["*.py", "*.sh", "Makefile", "*.R"])
    loop = bool(re.search(r"quarto\s+render.*(-P|--execute-params|-o|--output)", driver)) \
        or bool(re.search(r"(?i)for .* in .*region", driver)) and "quarto" in driver
    checks.append(Check("render_per_region_mechanism", loop, 1.0 if loop else 0.0, axis="repro"))
    if qmd:
        checks.append(best_effort_render(["quarto", "render", qmd.name, "-P",
                                          f"region:{T['regions'][0]}"], ws))
    return checks
