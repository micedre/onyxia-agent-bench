---
description: Scaffold a reproducible data science project (R or Python) following SSP Cloud conventions
agent: build
---

Scaffold a reproducible data science project in the current directory,
following the platform conventions (AGENTS.md and the skills mentioned below).

Requested by the user: $ARGUMENTS

Steps — ask about anything not already specified in the request above:

1. **Clarify** (one question, only if not deducible): language (Python or R),
   project name, and whether a Quarto report stub is wanted.
2. **Initialize Git** if not already a repo, with the data-science `.gitignore`
   from the `git-workflow-ds` skill (data/, environments, outputs, .env).
3. **Language setup**
   - Python: `uv init` (pyproject.toml + uv.lock), a `src/<pkg>/` layout,
     `ruff` and `pytest` configured in pyproject.toml, `tests/` with one
     passing placeholder test — follow the `python-datascience` skill.
   - R: `renv::init()`, `R/` for functions, `tests/testthat/` with one passing
     placeholder test, lintr/styler defaults — follow the `r-datascience` skill.
4. **Data conventions**: create `conf/config.yaml` with a parameterized S3
   path (`s3://<bucket>/...` placeholder, endpoint from `AWS_S3_ENDPOINT`) and
   a data-access stub following the `onyxia-storage-s3` skill. No data in Git.
5. **Notebook hygiene**: install the nbstripout Git filter — run the
   `git-workflow-ds` skill's `scripts/setup-nbstripout.sh` (Python projects, or
   whenever notebooks are expected).
6. **README.md**: project purpose, how to restore the environment
   (`uv sync` / `renv::restore()`), how to run tests, where the data lives (S3
   path, retrieval date), how to render the report if any.
7. **Optional Quarto stub**: if wanted, add a parameterized report from the
   `quarto-publication` skill's `assets/report-template.qmd` — see the
   **Python + Quarto specifics** below for the render toolchain and safety net.
8. **First commit**: atomic, message per `git-workflow-ds` conventions.
   Show `git status` first so the user can confirm nothing unwanted is staged.

**Python + Quarto specifics** — when the project renders reports, do all of the
following so rendering does not break on Onyxia (see AGENTS.md "Rendering
reports" and the `quarto-publication` skill):

1. Add the render toolchain as a `doc` dependency group:
   ```bash
   uv add --group doc jupyter nbclient ipykernel jupyter-cache tabulate
   uv add polars pyarrow                  # streaming reads from S3
   uv add geopandas pandera               # only for geo / data-validation projects
   ```
2. Copy `report-template.qmd` and `_environment` from the `quarto-publication`
   skill's `assets/` to the project root, and add a minimal `_quarto.yml`:
   ```yaml
   project:
     type: default
   execute:
     freeze: auto
     warning: false
   ```
   Quarto reads `_environment` (`QUARTO_PYTHON=.venv/bin/python`) only inside a
   project, so this `_quarto.yml` is what lets even a bare `quarto render` fall
   back to the venv. (Use the website/book `_quarto.yml` variant from the skill
   instead if a multi-page site is wanted.)
3. Add a `justfile` with a render target so nobody calls bare `quarto`:
   ```make
   render:
       uv run quarto render report-template.qmd
   ```
4. For any tabular deliverable, add a `schema.py` so bad units/ranges fail fast
   (follow the `data-validation` skill):
   ```python
   import pandera.pandas as pa   # older pandera: `import pandera as pa`

   schema = pa.DataFrameSchema({
       "taux_pauvrete_60": pa.Column(float, pa.Check.in_range(0, 100)),   # %
       "revenu_median":    pa.Column(float, pa.Check.in_range(0, 60_000)),# €/an
       "nb_menages":       pa.Column(int,   pa.Check.ge(0)),
   })
   ```

Finish by printing the created tree and the next manual steps (e.g. create the
remote repo, store any API key in Vault — `vault-secrets-onyxia` skill).
