"""Notation offline de T01 : on juge le CODE produit, sans toucher S3."""
from bench.grading import (
    Check, code_text, file_exists, references_s3, no_download_to_disk,
    env_vars_used, no_hardcoded_secrets,
)

def grade(ctx):
    checks = []
    checks.append(file_exists(ctx.workspace, ["*.py"], name="script_present",
                              axis="functional"))
    text = code_text(ctx.workspace, ["*.py"])
    checks.append(references_s3(text))                 # lit s3:// + duckdb/polars
    checks.append(no_download_to_disk(text))           # pas de download_file/fs.get
    checks.append(env_vars_used(text, ["AWS_S3_ENDPOINT", "AWS_ACCESS_KEY_ID"]))
    checks.append(no_hardcoded_secrets(text))
    return checks
