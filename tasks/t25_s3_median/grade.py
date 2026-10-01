"""Notation de T25 : le RESULTAT est juste (`mediane_departement.csv` vs verite terrain) ET le
CODE suit les conventions S3 de la plateforme (lecture en memoire depuis s3://, rien telecharge
sur disque, identifiants pris dans l'environnement, rien en dur)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from bench.grading import (
    Check,
    code_text,
    env_vars_used,
    file_exists,
    no_download_to_disk,
    no_hardcoded_secrets,
    references_s3,
)
from bench.outcome import table_check

TRUTH = json.loads((Path(__file__).parent / "ground_truth.json").read_text(encoding="utf-8"))

# Clients qui lisent AWS_* depuis l'environnement sans nommer la variable (cf. t01).
_IMPLICIT_CRED_CHAIN = [
    r"boto3\.(client|resource|Session)", r"s3fs\.S3FileSystem", r"fs\.S3FileSystem",
    r"pyarrow\.fs", r"CREDENTIAL_CHAIN|credential_chain", r"CREATE\s+SECRET.*TYPE\s+S3",
    r"fsspec\.filesystem\(\s*['\"]s3", r"storage_options", r"aws\.s3|arrow::s3_bucket",
]


def _credentials_from_env(text: str) -> Check:
    explicit = env_vars_used(text, ["AWS_S3_ENDPOINT", "AWS_ACCESS_KEY_ID",
                                    "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"])
    if explicit.passed:
        return explicit
    hit = next((p for p in _IMPLICIT_CRED_CHAIN if re.search(p, text, re.IGNORECASE)), None)
    if hit:
        return Check("reads_env_creds", True, 1.0, axis="platform",
                     detail=f"chaine d'identifiants implicite (env) : {hit}")
    return explicit


def grade(ctx):
    ws = ctx.workspace
    checks = [file_exists(ws, ["*.py"], name="script_present")]
    text = code_text(ws, ["*.py"])
    checks.append(references_s3(text))
    checks.append(no_download_to_disk(text))
    checks.append(_credentials_from_env(text))
    checks.append(no_hardcoded_secrets(text))
    checks += table_check(
        ws, "mediane_departement.csv", TRUTH["median_revenu_by_departement"],
        name="medians_correct", key_patterns=[r"depart", r"^dep", r"code"],
        val_patterns=[r"median", r"revenu"], rel_tol=0.0, abs_tol=0.06, weight=2.0,
        normalize_key=lambda s: s.zfill(2) if s.isdigit() else s)
    return checks
