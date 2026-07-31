---
name: python-datascience
description: Python data science / ML project standards on Onyxia, aligned with "Python pour la data science" (Lino Galiana, ENSAE) — uv environment, ruff quality, pytest tests, pandas/polars data wrangling, performant Parquet reading with pyarrow/duckdb, scikit-learn pipelines, serving via FastAPI. Load to create/structure a Python project, choose libraries, write clean Python ML code, or whenever the task mentions pyproject.toml, uv.lock, a notebook to industrialize, ruff, pytest or scikit-learn. (keywords: manipulation de données, librairies, industrialiser un notebook, mise à disposition, qualité de code)
license: MIT
---

# Python project standards (data science / ML) on Onyxia

Core reference: **"Python pour la data science"** (Python for data science) by
Lino Galiana (pythonds.linogaliana.fr), an ENSAE/Ensai course designed around the SSP Cloud.

## Environment & tooling — uv
`uv` is the recommended manager (deterministic lockfile, fast).
```bash
uv init my-project && cd my-project
uv add polars pandas pyarrow duckdb scikit-learn mlflow s3fs
uv add --dev ruff pytest mypy
uv run python scripts/train.py
uv sync                         # rebuilds the env from uv.lock (reproducible)
```
`pyproject.toml` + `uv.lock` are committed; never the `.venv/`.

## Code quality
```bash
uv run ruff format .            # formatting
uv run ruff check --fix .       # lint + safe fixes
uv run pytest -q                # tests
uv run mypy src/                # gradual typing
```

## Data wrangling: the right tool for the data volume
- **pandas**: comfort, small/medium volumes, rich ecosystem.
- **polars**: fast DataFrames, *lazy* (`scan_*`) on large volumes.
- **Parquet rather than CSV**: columnar, compressed, typed. To take advantage
  of it (column-only reads, *predicate pushdown*), read with **pyarrow.dataset**
  or **duckdb** rather than loading everything into a `DataFrame`:
```python
import pyarrow.dataset as ds, pyarrow.compute as pc
table = (ds.dataset("data/RP_partitionne", partitioning="hive")
           .to_table(filter=pc.field("DEPT").isin(["18","36"]), columns=["AGED","IPONDI","DEPT"]))
df = table.to_pandas()
```
```python
import duckdb
duckdb.sql("FROM read_parquet('data/RP.parquet') SELECT AGED, SUM(IPONDI) GROUP BY AGED").to_df()
```
- **Partition** a Parquet dataset (`pq.write_to_dataset(..., partition_cols=[...])`)
  when you often filter on a variable. S3 access: `onyxia-storage-s3` skill.

## Modeling — scikit-learn
- Encapsulate all preprocessing in a `Pipeline` + `ColumnTransformer`
  (prevents data leakage, makes the model deployable as a single unit).
- Split train/valid/test, fix a seed, validate with cross-validation,
  evaluate with a metric suited to the problem (not just accuracy).
- Track every trial with MLflow (`mlflow-tracking` skill).

## Serving a model — FastAPI
Expose predictions through a **FastAPI** API (loaded from the MLflow registry),
then containerize and deploy (`argo-mlops` skill). See the chapter
"Mettre à disposition un modèle par le biais d'une API" (serving a model
through an API) in the reference.

## Recommended structure
```
my-project/
├── pyproject.toml / uv.lock
├── src/my_project/        # importable code (data.py, features.py, model.py)
├── scripts/               # parameterized entry points (train.py, predict.py)
├── tests/
├── conf/                  # parameters (YAML), NO secrets
└── notebooks/             # exploration only (production logic -> src/)
```

## Principles (from the reference)
Modular code (short, testable functions), strict separation of code / config /
data (Git ≠ data storage → everything on S3), parameterized paths never hard-coded,
notebooks reserved for exploration. Git is essential — `.gitignore`, commits,
notebooks: `git-workflow-ds` skill. Publishing results: `quarto-publication`
skill.
