# Project context — Data science on Onyxia

This repository is worked on from an interactive service (VSCode, Jupyter or RStudio)
launched on **Onyxia** — the data science platform developed by Insee,
deployed here on the **SSP Cloud** (`datalab.sspcloud.fr`). All agents
must know and leverage this environment rather than proposing
"generic cloud" solutions.

## Guiding principles
- **Reproducibility first**: everything must be replayable identically
  (lockfiles, containers, declarative pipelines, data on S3, code under Git).
- **Open source**: R, Python, Quarto, Git; no proprietary dependency.
- **Confidentiality**: on the public instance, only public / non-sensitive
  data is allowed. Never write a secret in plain text in the code.
  The LLMs used here are **self-hosted on the platform**: data sent
  to the agents stays within the sspcloud perimeter.
- **From prototype to production**: we target the full MLOps cycle
  (experimentation → packaging → deployment → monitoring).

## Platform: what is already provided and preconfigured
- **Kubernetes** underneath; each service is a *Helm chart* from the catalog
  (`inseefrlab/...`). Two URL conventions coexist:
  - `https://user-<namespace>-<id>.user.lab.sspcloud.fr`: **auto-generated** URL
    of a service launched from the catalog (MLflow, VSCode, Jupyter…);
  - `https://<chosen-name>.lab.sspcloud.fr`: hostname **declared yourself** in
    a custom `Ingress` (e.g. prediction API deployed via GitOps with ArgoCD).
- **S3 storage = MinIO** (compatible with Amazon's S3 API). The personal bucket
  is named after the username. The `diffusion/` folder at the root of a bucket
  is **readable by all** users (sharing mechanism).
- **Vault** for secrets (tokens, passwords), injected as environment
  variables into services (details in the `vault-secrets-onyxia` skill).
- **MLflow**: shared instance for experiment tracking and the model
  registry (metadata in PostgreSQL, artifacts on MinIO).
- **Argo Workflows** (orchestration of parallel tasks on K8s) and **ArgoCD**
  (continuous deployment via GitOps) for industrialization.
- **duckdb** available in all interactive services; prefer it for
  processing large data (Parquet, lazy reading).

## Automatically injected environment variables
READ from the environment, NEVER hard-code:

| Variable | Role |
|---|---|
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` | temporary S3/MinIO token |
| `AWS_DEFAULT_REGION` | region (often `us-east-1` on the MinIO side) |
| `AWS_S3_ENDPOINT` | MinIO host (e.g. `minio.lab.sspcloud.fr`) |
| `MLFLOW_TRACKING_URI` | set when an MLflow service is running |
| `MLFLOW_S3_ENDPOINT_URL` | S3 endpoint for MLflow artifacts |
| `VAULT_ADDR`, `VAULT_TOKEN` | access to the Vault server (secrets) |
| `VAULT_MOUNT`, `VAULT_TOP_DIR` | mount point and root folder of your secrets (`vault-secrets-onyxia` skill) |

> **S3 token expiration (7 days)**: an expired token causes a **403** error
> on MinIO and the service shows up in red in "My services". Remedies:
> relaunch a service (new token) or re-inject fresh tokens. If an agent
> sees a 403 on S3, suspect expiration before any other diagnosis.

## Data access (summary — details in the `onyxia-storage-s3` skill)

SSP Cloud MinIO endpoint: `https://minio.lab.sspcloud.fr` (= `$AWS_S3_ENDPOINT`).
**Onyxia rule**: do not download files into the container, **ingest the
data directly into memory** from S3; only copy locally if necessary.

- **Python**: `s3fs` to read/write in memory; `duckdb` for large
  Parquet (lazy reading, *predicate pushdown*).
- **R**: `duckdb` (reading Parquet/dataset on S3) or `aws.s3` (the AWS_* variables suffice).
- **Terminal**: **`aws s3`** is the preferred CLI (the `mc` client also exists):
  `aws --endpoint-url "https://$AWS_S3_ENDPOINT" s3 ls s3://$USERNAME/`.

## Expected working conventions
- Python: project managed with **`uv`** (`pyproject.toml` + `uv.lock`), formatted/linted
  with **`ruff`**, tested with **`pytest`**. Prefer `polars`/`duckdb` for large data volumes.
- R: environment pinned with **`renv`**, pipelines with **`targets`** only if strictly necessary, tests with
  **`testthat`**, `tidyverse`/`styler` style.
- Data: no large files in Git → everything on S3; parameterized paths.
- Documentation and reporting: **Quarto**.
- No secret committed. No data in the repository.

## Rendering reports (Quarto) — critical, applies to every Python/R report

Rendering is where projects most often break on Onyxia. Before any render:

- **Always render inside the project environment.** A bare `quarto render`
  picks up the system interpreter (`/opt/python`), *not* your `.venv`, and
  fails with "unactivated Python environment in .venv".
  - Python: `uv run quarto render report.qmd` (never bare `quarto render`).
  - Safety net: the `/new-project` scaffold drops an `_environment` file
    (`QUARTO_PYTHON=.venv/bin/python`) plus a minimal `_quarto.yml`. Quarto
    reads `_environment` only inside a project, so **both** are needed for a
    bare render to fall back to the venv — prefer `uv run` regardless.
  - R: render from the `renv`-activated session.
- **The env must contain the rendering toolchain.** Quarto needs `jupyter`,
  `nbclient`, `ipykernel` to execute Python cells; `tabulate` is required for
  `df.to_markdown()`. These live in the project's `doc` dependency group —
  never assume the system Python has them.
- **Cell options are Jupyter dialect, not knitr.** Use `#| echo: false`,
  `#| output: asis`, `#| fig-cap:` — **never** `#| results: asis` (that is R).
- **Embedding a figure:** draw it in a `{python}` cell and let the cell emit it
  (add `#| fig-cap:`), or save it and reference `![](path.png)`. Never use
  `IPython.display.Image` to embed a static image — it does not render.
- **Injecting a computed value into prose:** use Quarto inline code
  `` `{python} f"{value:.1f}"` ``. Do NOT write `{value}` as literal text, do
  NOT invent `quarto.doc.variables(...)` (it does not exist), and do NOT
  install `quarto`/`quartodoc` from PyPI (unrelated packages).
- A known-good, end-to-end template lives in the `quarto-publication` skill
  (`assets/report-template.qmd` + `assets/_environment`). Copy it rather than
  reconstructing these patterns.

## Data integrity & S3 streaming — non-negotiable

- **Every number, table and value in a report is computed from the data at
  render time.** Never transcribe, guess, or hard-code figures into the
  document or a `params`/YAML file. A precomputed value must come from a
  reproducible pipeline artifact, not be typed by the agent.
- Before reporting a statistic, sanity-check unit and range (a monthly median
  disposable income is in €, not thousands; a rate is in %). Encode these as
  `pandera`/`pointblank` checks (skill `data-validation`).
- If two computations disagree, STOP and reconcile — report neither number
  until the discrepancy is understood.
- **Stream from S3 first, copy locally last.** Read data lazily/in memory from
  S3 (`s3fs`, `duckdb`, `polars scan_parquet`, `arrow`) before anything else;
  only `aws s3 cp` to local disk when a tool genuinely requires a file on disk
  (details and recipes in the `onyxia-storage-s3` skill).

## Internal references (authoritative)
- SSP Cloud platform docs: https://docs.sspcloud.fr
- Onyxia user guide: https://docs.onyxia.sh/user-doc/user-guide
- **R** — utilitR (Insee best practices): https://book.utilitr.org
- **Python** — "Python pour la data science" (Python for data science, L. Galiana): https://pythonds.linogaliana.fr
- MLOps: https://github.com/InseeFrLab/formation-mlops
- Production deployment / reproducibility: https://ensae-reproductibilite.github.io/website
- Data science Docker images: https://github.com/inseefrlab/images-datascience

When a task falls within a tooled domain, **load the corresponding skill**
(`onyxia-storage-s3`, `mlflow-tracking`, `argo-mlops`, `r-datascience`,
`python-datascience`, `onyxia-reproducibility`, `quarto-publication`,
`data-validation`, `geodata`, `vault-secrets-onyxia`, `git-workflow-ds`)
before producing code.
