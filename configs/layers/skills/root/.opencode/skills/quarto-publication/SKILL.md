---
name: quarto-publication
description: Author AND render/publish Quarto documents (.qmd) on Onyxia (Python & R) — HTML/PDF reports, reveal.js presentations, dashboards, websites and books; running `quarto render` in the RIGHT environment (uv/renv), embedding figures and tables, injecting computed values, parameterized documents, publishing to S3 (diffusion/ folder) or GitHub Pages. Load whenever a report, notice, presentation, dashboard or documentation must be produced or turned into a document, or when the task mentions Quarto, .qmd, "render", "quarto render", migrating an .Rmd, or an HTML/PDF render (rapport, publier, présentation, tableau de bord).
license: MIT
---

# Writing and publishing with Quarto on Onyxia

Quarto is the recommended reporting tool (AGENTS.md): the code (R and/or
Python) stays executable and versioned **with** the report. An existing
`.Rmd` usually migrates by renaming it to `.qmd` and adapting the header.

> **Golden rule**: the render must be **re-generable** (`quarto render`) —
> never hand-pasted results, never a secret or sensitive data in the
> rendered document.

## Critical patterns on Onyxia (read before rendering)

These are the traps that most often make a render fail on the platform:

1. **Render inside the project env.** Python: `uv run quarto render report.qmd`
   — a bare `quarto render` uses `/opt/python`, not `.venv`, and fails with
   "unactivated Python environment in .venv". Belt-and-suspenders: keep an
   `assets/_environment` file (`QUARTO_PYTHON=.venv/bin/python`) **and** a
   `_quarto.yml` at the root — Quarto reads `_environment` only inside a
   project — but `uv run` is the reliable habit. R: render from the
   `renv`-activated session.
2. **Env needs the render toolchain**: `jupyter`, `nbclient`, `ipykernel`
   (execute Python cells) and `tabulate` (for `df.to_markdown()`) — keep them
   in the project's `doc` dependency group, never assume the system Python has
   them.
3. **Cell options are Jupyter dialect**: `#| echo: false`, `#| output: asis`,
   `#| fig-cap:`. NOT `#| results: asis` (that is knitr/R).
4. **Figures**: draw in a `{python}` cell + `plt.show()` (or `![](path.png)`),
   never `IPython.display.Image` (does not render a static image).
5. **Values in prose**: inline `` `{python} f"{x:.1f}"` ``. Not literal `{x}`,
   not `@x`, not `quarto.doc.variables(...)` (does not exist). And never `pip
   install quarto`/`quartodoc` (unrelated PyPI packages).

**Copy `assets/report-template.qmd` + `assets/_environment` as a starting
point** — they render end-to-end on Onyxia and demonstrate all of the above.
Do not reconstruct these from memory.

## Core workflow

1. **Create** a `.qmd` (or a project with `_quarto.yml`); read data
   **from S3** (skill `onyxia-storage-s3`), never from a hard-coded local path.
2. **Render / preview** — always inside the project env (see Critical patterns):
   ```bash
   uv run quarto render report.qmd      # Python: -> report.html (or pdf). Never bare `quarto render`.
   uv run quarto render report.qmd --to pdf
   uv run quarto preview report.qmd     # live render while writing
   ```
   With a `_quarto.yml` project file present, `assets/_environment`
   (`QUARTO_PYTHON=.venv/bin/python`) makes even a bare `quarto render` fall
   back to the venv. R: render from the `renv`-activated session
   (`quarto render`).
3. **Publish**: copy to the S3 `diffusion/` folder or `quarto publish gh-pages`
   (recipes in [references/formats.md](references/formats.md)).

## Choosing a format

| Deliverable | Format |
|---|---|
| Analysis report, notice | single `.qmd`, `format: html` (or `pdf`) |
| Presentation | `format: revealjs` |
| Monitoring dashboard | `format: dashboard` |
| Multi-page documentation / site | project `type: website` |
| Structured long-form (chapters) | project `type: book` |
| Same report for several years/departments | parameterized document (`params`) |

## Guardrails

- `freeze: auto` in projects: avoids re-running expensive unchanged chunks.
- The `.qmd` is committed; the render (`.html`, `_site/`) goes to `output/`
  or to S3, not into Git (skill `git-workflow-ds`).
- Proofread the render before publishing: no secret, no individual-level data.

## Where to look next

| Need | Read |
|---|---|
| `.qmd` anatomy, website/book/dashboard/revealjs recipes, parameterized documents, publish targets (S3 `diffusion/`, GitHub Pages), diagnostics | [references/formats.md](references/formats.md) |
| Starter project config (website) | [assets/_quarto.yml](assets/_quarto.yml) |
| Starter parameterized report (Python engine, renders end-to-end) | [assets/report-template.qmd](assets/report-template.qmd) |
| Interpreter safety net (`QUARTO_PYTHON`) — copy to project root | [assets/_environment](assets/_environment) |

## References

- https://quarto.org/docs/guide/
- utilitR, "Produire des documents" section: https://book.utilitr.org
