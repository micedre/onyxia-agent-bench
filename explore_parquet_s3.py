"""
Explore a partitioned Parquet dataset on S3/MinIO using DuckDB.

This script:
1. Lists files in a partitioned S3 dataset
2. Reads a sample of the data (first 100 rows)
3. Identifies column types and their semantic roles
4. Determines the partition structure

Usage:
    python explore_parquet_s3.py

Environment variables required:
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN,
    AWS_S3_ENDPOINT, AWS_DEFAULT_REGION
"""

import duckdb
import os
import sys


def setup_duckdb_s3() -> duckdb.DuckDBPyConnection:
    """Configure DuckDB with S3 credentials from environment variables."""
    conn = duckdb.connect()

    ak = os.environ.get("AWS_ACCESS_KEY_ID", "")
    sk = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    token = os.environ.get("AWS_SESSION_TOKEN", "")
    endpoint = os.environ.get("AWS_S3_ENDPOINT", "minio.lab.sspcloud.fr")
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

    conn.execute(f"""SET s3_access_key_id='{ak}'""")
    conn.execute(f"""SET s3_secret_access_key='{sk}'""")
    if token:
        conn.execute(f"""SET s3_session_token='{token}'""")
    conn.execute(f"""SET s3_endpoint='{endpoint}'""")
    conn.execute(f"""SET s3_url_style='path'""")
    conn.execute(f"""SET s3_region='{region}'""")
    conn.execute("""SET s3_use_ssl=false""")

    return conn


def list_files(conn: duckdb.DuckDBPyConnection, s3_path: str):
    """
    Try to list files in an S3 prefix using various DuckDB approaches.

     Returns a tuple of (files: list[str], common_prefixes: list[str]).
    """
    files = []
    prefixes = []

    # Approach 1: read_parquet with glob (if it works)
    try:
        result = conn.execute(f"""
            SELECT file_name, file_size, num_row_groups
            FROM read_parquet('{s3_path}/**')
        """).fetchall()
        if result:
            files = [r[0] for r in result]
            return files, prefixes
    except Exception:
        pass

    # Approach 2: Try parquet_scan with a known file pattern
    try:
        # This will fail if the glob doesn't expand, but gives metadata
        result = conn.execute(f"""
            DESCRIBE SELECT * FROM read_parquet('{s3_path}/**/*.parquet')
        """).fetchall()
        return result  # returns schema rows
    except Exception:
        pass

    return files, prefixes


def explore_schema(conn: duckdb.DuckDBPyConnection, s3_path: str):
    """Get the schema of the Parquet dataset."""
    try:
        result = conn.execute(f"""
            DESCRIBE SELECT * FROM read_parquet('{s3_path}')
        """).fetchall()
        columns = [(row[0], row[1]) for row in result]
        print("=" * 70)
        print("SCHEMA")
        print("=" * 70)
        for col_name, col_type in columns:
            print(f"  {col_name:40s} {col_type}")
        return columns
    except Exception as e:
        print(f"Could not get schema: {type(e).__name__}: {e}")
        return None


def sample_data(conn: duckdb.DuckDBPyConnection, s3_path: str, n: int = 100):
    """Read a sample of rows from the Parquet dataset."""
    try:
        df = conn.execute(f"""
            SELECT * FROM read_parquet('{s3_path}') LIMIT {n}
        """).fetchdf()

        print(f"\n{'=' * 70}")
        print(f"_SAMPLE ({len(df)} rows, {len(df.columns)} columns)")
        print(f"{'=' * 70}")
        print(df.to_string())
        return df
    except Exception as e:
        print(f"Could not read sample: {type(e).__name__}: {e}")
        return None


def count_rows(conn: duckdb.DuckDBPyConnection, s3_path: str):
    """Count total rows in the Parquet dataset."""
    try:
        count = conn.execute(f"""
            SELECT COUNT(*) FROM read_parquet('{s3_path}')
        """).fetchone()[0]
        return count
    except Exception as e:
        print(f"Could not count rows: {type(e).__name__}: {e}")
        return None


def identify_partition_columns(columns: list[tuple[str, str]]):
    """
    Identify which columns look like they correspond to:
    - department codes
    - commune codes
    - population count
    - income / revenu variables
    """
    categories = {
        "Department codes": [],
        "Commune codes": [],
        "Population": [],
        "Income / Revenu": [],
    }

    dept_patterns = [
        "dep", "dept", "departement", "code_dep",
        "codgeo", "geo", "codgeo", "dpt",
    ]
    commune_patterns = [
        "comm", "com", "code_comm", "co", "codcom",
        "iris", "loc", "geo",
    ]
    pop_patterns = [
        "pop", "nb", "count", "total", "habitants",
        "nombre", "population", "indpop",
    ]
    income_patterns = [
        "rev", "inc", "income", "revenu", "sal", "salaire",
        "med", "median", "mean", "moy", "pa", "priv",
        "dispo", "bi", "ca", "ca2",
    ]

    for col_name, _ in columns:
        col_lower = col_name.lower().replace("-", "_").replace(" ", "_")
        if any(p in col_lower for p in dept_patterns):
            categories["Department codes"].append(col_name)
        elif any(p in col_lower for p in commune_patterns):
            categories["Commune codes"].append(col_name)
        elif any(p in col_lower for p in pop_patterns):
            categories["Population"].append(col_name)
        elif any(p in col_lower for p in income_patterns):
            categories["Income / Revenu"].append(col_name)

    return categories


def describe_dialect(conn: duckdb.DuckDBPyConnection, s3_path: str):
    """Get detailed column information including null counts and stats."""
    try:
        result = conn.execute(f"""
            SELECT
                column_name,
                column_type,
                null_count,
                distinct_count,
                min_value,
                max_value
            FROM (
                SELECT * FROM read_parquet('{s3_path}')
            )
        """).fetchall()
        return result
    except Exception as e:
        print(f"Could not get column stats: {type(e).__name__}: {e}")
        return None


if __name__ == "__main__":
    # Dataset path — can be overridden via environment variable
    bucket = os.environ.get("S3_BUCKET", "mon-bucket")
    prefix = os.environ.get("S3_PREFIX", "data/census")
    s3_path = f"s3://{bucket}/{prefix}"

    print(f"Exploring: {s3_path}")
    print()

    conn = setup_duckdb_s3()

    # Step 1: Schema
    columns = explore_schema(conn, s3_path)

    # Step 2: Sample data
    df = sample_data(conn, s3_path)

    # Step 3: Row count
    total = count_rows(conn, s3_path)
    if total is not None:
        print(f"\n{'=' * 70}")
        print(f"TOTAL ROWS: {total:,}")
        print(f"{'=' * 70}")

    # Step 4: Column classification
    if columns:
        print(f"\n{'=' * 70}")
        print("COLUM_ ANALYSIS")
        print(f"{'=' * 70}")
        categories = identify_partition_columns(columns)
        for cat, cols in categories.items():
            marker = " [PARTITION]" if cat == "Department codes" or cat == "Commune codes" else ""
            if cols:
                print(f"\n  {cat}{marker}:")
                for c in cols:
                    print(f"    - {c}")
            else:
                print(f"\n  {cat}: (none detected)")

    # Step 5: Attempt to list files (DuckDB approach)
    print(f"\n{'=' * 70}")
    print("FILE_LISTING_ATTEMPT")
    print(f"{'=' * 70}")
    files, prefixes = list_files(conn, s3_path)
    if files:
        print(f"  Found {len(files)} parquet files:")
        for f in sorted(files)[:20]:
            print(f"    {f}")
        if len(files) > 20:
            print(f"    ... and {len(files) - 20} more")
    elif prefixes:
        print(f"  Schema-only (no sample row): got {len(prefixes)} type descriptions")
    else:
        print("  Could not list files — S3 credentials may be expired/invalid.")
        print("  This is expected if AWS_SESSION_TOKEN has expired (7-day lifetime on Onyxia).")
        print()
        print("  Resolution: relaunch the service to get fresh credentials, or")
        print("  set a new AWS_SESSION_TOKEN via Vault injection.")

    conn.close()
