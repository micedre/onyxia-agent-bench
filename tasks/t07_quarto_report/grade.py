"""Notation offline de T07 : presence + frontmatter d'un .qmd, rendu best-effort (le check
de rendu est neutre si `quarto` n'est pas installe dans ce sandbox)."""
from bench.grading import Check, best_effort_render, file_exists


def grade(ctx):
    checks = []
    checks.append(file_exists(ctx.workspace, ["*.qmd"], name="qmd_present"))
    checks.append(file_exists(ctx.workspace, ["*.qmd"], name="frontmatter_format",
                              needle="format:"))
    qmd_files = list(ctx.workspace.rglob("*.qmd"))
    if qmd_files:
        checks.append(best_effort_render(["quarto", "render", qmd_files[0].name], ctx.workspace))
    else:
        checks.append(Check("renders", False, 0.0, axis="functional", detail="pas de .qmd a rendre"))
    return checks
