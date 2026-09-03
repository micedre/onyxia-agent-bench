"""Notation de T07 : un .qmd livrable (pas le gabarit d'une skill, pas un brouillon), avec
frontmatter, qui lit bien les donnees, se rend en HTML et contient une figure."""
from __future__ import annotations

import re

import yaml

from bench.grading import Check, _rel, best_effort_render, deliverable_files, skipped


def _frontmatter(text: str) -> dict | None:
    m = re.match(r"\s*---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return None
    try:
        fm = yaml.safe_load(m.group(1))
        return fm if isinstance(fm, dict) else None
    except yaml.YAMLError:
        return None


def grade(ctx):
    ws = ctx.workspace
    checks = []
    qmds = deliverable_files(ws, ["*.qmd"])
    checks.append(Check("qmd_present", bool(qmds), 1.0 if qmds else 0.0,
                        detail=", ".join(str(_rel(ws, q)) for q in qmds) or "aucun .qmd livrable"))
    if not qmds:
        checks += [Check("frontmatter_format", False, 0.0, detail="-"),
                   Check("uses_data", False, 0.0, detail="-"),
                   Check("renders", False, 0.0, detail="pas de .qmd a rendre"),
                   Check("figure_in_output", False, 0.0, detail="-")]
        return checks
    texts = {q: q.read_text(encoding="utf-8", errors="replace") for q in qmds}
    fms = {q: _frontmatter(t) for q, t in texts.items()}
    fm_ok = any(fm and "format" in fm for fm in fms.values())
    checks.append(Check("frontmatter_format", fm_ok, 1.0 if fm_ok else 0.0,
                        detail="format: present" if fm_ok else "pas de frontmatter YAML avec `format`"))
    uses = any("donnees_insee" in t for t in texts.values())
    checks.append(Check("uses_data", uses, 1.0 if uses else 0.0,
                        detail="lit donnees_insee.csv" if uses else "ne lit pas donnees_insee.csv"))

    before = {p for p in ws.rglob("*.html")}
    render = None
    for q in qmds[:3]:
        render = best_effort_render(["quarto", "render", str(_rel(ws, q))], ws)
        if render.axis == "skipped" or render.passed:
            break
    checks.append(render)
    if render.axis == "skipped":
        checks.append(skipped("figure_in_output", "rendu impossible dans ce sandbox"))
        return checks
    new_html = [p for p in ws.rglob("*.html") if p not in before and ".venv" not in p.parts]
    fig = False
    for p in new_html:
        html = p.read_text(encoding="utf-8", errors="replace")
        if re.search(r"<img\b|<svg\b|plotly|class=\"figure|cell-output-display", html):
            fig = True
            break
    checks.append(Check("figure_in_output", fig, 1.0 if fig else 0.0,
                        detail=f"{len(new_html)} HTML produit(s), figure={'oui' if fig else 'non'}"))
    return checks
