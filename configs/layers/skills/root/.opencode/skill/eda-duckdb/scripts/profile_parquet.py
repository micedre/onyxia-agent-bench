#!/usr/bin/env python3
"""Profile a Parquet (or CSV) dataset on S3/MinIO — or local — with duckdb.

Usage:
    python profile_parquet.py s3://my-bucket/path/file.parquet
    python profile_parquet.py "s3://my-bucket/dataset/*.parquet"
    python profile_parquet.py data/local.parquet

Reads lazily (nothing is downloaded); credentials come from the standard
Onyxia-injected AWS_* environment variables. On a 403, suspect the 7-day
token expiry first (see the onyxia-storage-s3 skill).
"""

import os
import sys

try:
    import duckdb
except ImportError:
    sys.exit("duckdb is not installed. Run: uv pip install duckdb  (or pip install duckdb)")

TOP_N_CATEGORICAL = 10
CATEGORICAL_MAX_CARDINALITY = 50


def connect(path: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    if path.startswith("s3://"):
        con.execute("INSTALL httpfs; LOAD httpfs;")
        endpoint = os.environ.get("AWS_S3_ENDPOINT")
        if not endpoint:
            sys.exit("AWS_S3_ENDPOINT is not set — are you on an Onyxia service? "
                     "(run the onyxia-storage-s3 skill's scripts/check_s3.sh)")
        # getenv() only exists in the duckdb CLI — from Python, bind values
        # from os.environ (kept in memory, never written anywhere).
        con.execute(
            """
            CREATE OR REPLACE SECRET onyxia (
                TYPE S3,
                KEY_ID   $key_id,
                SECRET   $secret,
                SESSION_TOKEN $token,
                ENDPOINT $endpoint,
                URL_STYLE 'path'
            );
            """,
            {
                "key_id": os.environ.get("AWS_ACCESS_KEY_ID", ""),
                "secret": os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
                "token": os.environ.get("AWS_SESSION_TOKEN", ""),
                "endpoint": endpoint,
            },
        )
    return con


def sql_literal(value: str) -> str:
    """Quote a value as a SQL string literal (duckdb escapes ' by doubling it).

    The path comes from argv and cannot be passed as a bind parameter here — it
    is part of the FROM clause, not a value — so it must be escaped by hand.
    """
    return "'" + value.replace("'", "''") + "'"


def sql_identifier(name: str) -> str:
    """Quote a column name as a SQL identifier (duckdb doubles the ")."""
    return '"' + name.replace('"', '""') + '"'


def source_sql(path: str) -> str:
    lit = sql_literal(path)
    if path.endswith((".csv", ".csv.gz")):
        return f"read_csv({lit}, sample_size = -1)"
    return f"read_parquet({lit}, hive_partitioning = true)"


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    path = sys.argv[1]
    src = source_sql(path)
    con = connect(path)

    def q(sql: str):
        return con.sql(sql)

    print(f"# Profile of {path}\n")

    n_rows = q(f"SELECT count(*) AS n FROM {src}").fetchone()[0]
    schema = q(f"DESCRIBE SELECT * FROM {src}").fetchall()
    print(f"## Shape\n{n_rows:,} rows × {len(schema)} columns\n")

    print("## Schema")
    for name, dtype, *_ in schema:
        print(f"  {name:<40} {dtype}")
    print()

    print("## Column summary (nulls, uniques, min/max, quartiles)")
    q(f"SUMMARIZE SELECT * FROM {src}").show(max_width=200, max_rows=len(schema) + 5)

    # Candidate keys: columns whose approximate distinct count ≈ row count
    print("## Candidate keys (approx_unique ≈ row count)")
    summary = q(f"SUMMARIZE SELECT * FROM {src}").fetchall()
    cols = [d[0] for d in q(f"SUMMARIZE SELECT * FROM {src}").description]
    idx = {c: i for i, c in enumerate(cols)}
    found_key = False
    for row in summary:
        approx = row[idx["approx_unique"]]
        if approx and n_rows and int(approx) >= 0.99 * n_rows:
            print(f"  {row[idx['column_name']]}")
            found_key = True
    if not found_key:
        print("  (none — no single column is near-unique)")
    print()

    print(f"## Top {TOP_N_CATEGORICAL} values of low-cardinality text columns")
    for row in summary:
        col, ctype = row[idx["column_name"]], row[idx["column_type"]]
        approx = row[idx["approx_unique"]]
        if "VARCHAR" in str(ctype) and approx and int(approx) <= CATEGORICAL_MAX_CARDINALITY:
            print(f"\n### {col}")
            q(
                f"SELECT {sql_identifier(col)}, count(*) AS n, "
                f"round(100.0 * count(*) / {n_rows}, 1) AS pct "
                f"FROM {src} GROUP BY 1 ORDER BY 2 DESC LIMIT {TOP_N_CATEGORICAL}"
            ).show()

    print("\nDone. Dig deeper where null rates, cardinalities, or ranges look suspicious.")


if __name__ == "__main__":
    main()
