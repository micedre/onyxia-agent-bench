"""Notation de T07 : un .qmd livrable (pas le gabarit d'une skill, pas un brouillon), avec
frontmatter, qui lit bien les donnees, et dont le rendu HTML existe avec une figure.

Le rendu est d'abord cherche parmi les fichiers LIVRES par l'agent : il tourne dans l'image de
la plateforme (R + Python + quarto) alors que l'hote de notation n'a pas forcement R. Un
rapport knitr rendu avec succes dans le pod etait sinon note 0 parce que `quarto render`
echouait ici (constate sur un run reel, 3 cellules sur 3). Anti-triche : le HTML doit porter le
nom du .qmd, contenir une figure ET une valeur issue des donnees - pas juste exister."""
from __future__ import annotations

import csv
import re
from pathlib import Path

import yaml

from bench.grading import (
    Check,
    _iter_files,
    _rel,
    best_effort_render,
    deliverable_files,
    skipped,
)

FIGURE_RE = re.compile(r"<img\b|<svg\b|plotly|class=\"figure|cell-output-display|figure-html")


def _frontmatter(text: str) -> dict | None:
    m = re.match(r"\s*---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return None
    try:
        fm = yaml.safe_load(m.group(1))
        return fm if isinstance(fm, dict) else None
    except yaml.YAMLError:
        return None


def _needs_r(qmd_text: str) -> bool:
    """Document a moteur knitr : chunks ```{r} ou engine/knitr declare."""
    fm = _frontmatter(qmd_text) or {}
    if str(fm.get("engine", "")).lower() == "knitr" or "knitr" in str(fm.get("execute", "")):
        return True
    return bool(re.search(r"^```\{r[ ,}]", qmd_text, re.MULTILINE))


def _data_tokens(ws: Path) -> list[str]:
    """Quelques valeurs des donnees source, pour verifier qu'un HTML est bien LE rapport."""
    csv_path = ws / "donnees_insee.csv"
    if not csv_path.is_file():
        return []
    toks = []
    with csv_path.open(encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("commune") or "").strip()
            if name:
                toks.append(name)
    return toks


def _delivered_render(ws: Path, qmds: list[Path]) -> tuple[Path | None, bool, str]:
    """(html, figure_presente, detail) pour le rendu livre par l'agent, s'il est credible."""
    tokens = _data_tokens(ws)
    stems = {q.stem for q in qmds}
    htmls = [p for p in _iter_files(ws, ["*.html"], include_scratch=True)
             if ".venv" not in p.parts]
    for p in sorted(htmls, key=lambda x: (x.stem not in stems, str(x))):
        try:
            html = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        same_name = p.stem in stems
        has_fig = bool(FIGURE_RE.search(html))
        has_data = any(t and t in html for t in tokens) if tokens else True
        if same_name and has_data:
            return p, has_fig, (f"{_rel(ws, p)} livre par l'agent (nom du .qmd, donnees "
                                f"presentes, figure={'oui' if has_fig else 'non'})")
    return None, False, "aucun HTML livre correspondant au .qmd et aux donnees"


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

    # 1) le rendu livre par l'agent fait foi
    html, has_fig, detail = _delivered_render(ws, qmds)
    if html is not None:
        checks.append(Check("renders", True, 1.0, detail=detail))
        checks.append(Check("figure_in_output", has_fig, 1.0 if has_fig else 0.0, detail=detail))
        return checks

    # 2) sinon on tente le rendu ici ; neutre si le moteur du document manque sur cet hote
    before = set(ws.rglob("*.html"))
    render = None
    for q in qmds[:3]:
        requires = ("Rscript",) if _needs_r(texts[q]) else ()
        render = best_effort_render(["quarto", "render", str(_rel(ws, q))], ws,
                                    requires=requires)
        if render.axis == "skipped" or render.passed:
            break
    checks.append(render)
    if render.axis == "skipped":
        checks.append(skipped("figure_in_output", "rendu impossible dans ce sandbox"))
        return checks
    new_html = [p for p in ws.rglob("*.html") if p not in before and ".venv" not in p.parts]
    fig = any(FIGURE_RE.search(p.read_text(encoding="utf-8", errors="replace"))
              for p in new_html)
    checks.append(Check("figure_in_output", fig, 1.0 if fig else 0.0,
                        detail=f"{len(new_html)} HTML produit(s), figure={'oui' if fig else 'non'}"))
    return checks
