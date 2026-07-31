---
name: onyxia-storage-s3
description: Read and write data on Onyxia's S3/MinIO object storage (SSP Cloud), in Python (s3fs, polars, duckdb, pyarrow), in R (duckdb, arrow, aws.s3) and via the aws s3 CLI. Load whenever a task reads or writes data, mentions S3, MinIO, a bucket, remote Parquet/CSV files, the diffusion folder, or a 403 error on storage. (Mots-clés français : stockage, compartiment, lire un parquet sur S3, MinIO, dossier diffusion)
license: MIT
---

# S3/MinIO storage access on Onyxia

The datalab uses **MinIO** (Amazon S3-compatible API). Credentials are injected
automatically into the service at creation time — **never hardcode them**.
SSP Cloud MinIO endpoint: `https://minio.lab.sspcloud.fr`.

## Golden rules
1. **Never hardcode credentials** — always read the injected `AWS_*`
   environment variables.
2. **Do not download files into the container**: ingest data directly
   in memory / lazily from S3 (`s3fs`, `arrow`, `duckdb`). Only copy
   locally (`aws s3 cp`) if a tool truly requires a file on disk.
3. **Parquet first**: prefer Parquet + lazy readers (duckdb, polars,
   arrow) — column pruning and predicate pushdown mean only the useful
   data reaches memory.
4. The `diffusion/` folder at the root of a bucket is **readable by all
   authenticated users** (sharing / collaboration / reproducibility
   mechanism).

## Injected environment variables
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`
  — temporary S3/MinIO token
- `AWS_DEFAULT_REGION` — region (usually `us-east-1` on MinIO)
- `AWS_S3_ENDPOINT` — MinIO host only, no scheme (e.g. `minio.lab.sspcloud.fr`)

Personal bucket = SSP Cloud username. Careful: on pods, `$USERNAME` is the
generic `onyxia` user — the real username is in `$VAULT_TOP_DIR` (or
`$KUBERNETES_NAMESPACE` minus its `user-` prefix). Token is valid for **7 days**.

## First-hypothesis diagnostic: 403 = expired token
A **403 / AccessDenied / ExpiredToken** error on MinIO almost always means
the 7-day S3 token has expired (the service also shows red in "My services").
Suspect expiration **before any other diagnosis**. Remedies:
- Renew credentials from the Onyxia console: **"My account" → "Connect to
  storage"** page (fresh tokens to re-export), or
- Save code/data and **relaunch the service** (a new service gets a fresh
  token).

Other frequent pitfalls:
- **Endpoint**: always `https://$AWS_S3_ENDPOINT` — a URL without the
  scheme fails.
- **region**: with MinIO, use `region = ""` (R) to avoid spurious AWS
  region resolution.

## Recipes — where to look

| Need | Go to |
|---|---|
| Python recipes (s3fs, polars `scan_parquet`, duckdb httpfs, pyarrow) | [references/python.md](references/python.md) |
| R recipes (duckdb + secrets, aws.s3, arrow) | [references/r.md](references/r.md) |
| CLI recipes (`aws s3`, `mc`) | [references/cli.md](references/cli.md) |
| Automated diagnostic | run [scripts/check_s3.sh](scripts/check_s3.sh) |

## Sharing / collaboration
Dropping files under `s3://<bucket>/diffusion/` makes them readable by
everyone. For a collaborative project, agree on one member's bucket and put
the data in its `diffusion/` folder; production code stays on Git.

## References
- docs.sspcloud.fr/content/storage.html
- "Python pour la data science" (L. Galiana), Parquet & cloud chapter:
  pythonds.linogaliana.fr/content/manipulation/05_parquet_s3.html
