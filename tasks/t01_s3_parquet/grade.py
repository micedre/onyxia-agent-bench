"""Notation de T01 : le CODE suit les conventions S3 de la plateforme (statique) ET produit
les bons chiffres quand on l'execute sur le miroir local (CENSUS_URI=data/census).
La verite terrain est dans expected.json (hors fixtures, l'agent ne la voit pas)."""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from bench.grading import (
    Check,
    _pick_entry_script,
    _rel,
    _tail,
    code_contains,
    code_text,
    deliverable_files,
    env_vars_used,
    file_exists,
    no_download_to_disk,
    no_hardcoded_secrets,
    references_s3,
    run_in_project,
)

HERE = Path(__file__).resolve().parent
EXPECTED = json.loads((HERE / "expected.json").read_text(encoding="utf-8"))

# Clients/moteurs qui lisent AWS_ACCESS_KEY_ID/SECRET/S3_ENDPOINT depuis l'environnement sans
# qu'on ait a nommer la variable : c'est la bonne pratique sur la plateforme (rien en dur, rien
# a passer), donc exiger le nom litteral de la variable etait un faux negatif - constate sur un
# run reel ou 4/4 cellules utilisaient boto3/s3fs et etaient notees 0.
_IMPLICIT_CRED_CHAIN = [
    r"boto3\.(client|resource|Session)", r"botocore", r"s3fs\.S3FileSystem", r"s3fs\.core",
    r"fs\.S3FileSystem", r"pyarrow\.fs", r"CREDENTIAL_CHAIN|credential_chain",
    r"CREATE\s+SECRET.*TYPE\s+S3", r"fsspec\.filesystem\(\s*['\"]s3", r"storage_options",
    r"aws\.s3|arrow::s3_bucket", r"AWS_PROFILE|~/\.aws",
]


def _s3_credentials_ok(text: str) -> Check:
    """Les identifiants S3 viennent de l'environnement : soit une variable nommee explicitement,
    soit une chaine d'identifiants implicite (boto3/s3fs/pyarrow/duckdb). Dans les deux cas rien
    en dur - c'est `no_hardcoded_secret` qui garde cette exigence."""
    explicit = env_vars_used(text, ["AWS_S3_ENDPOINT", "AWS_ACCESS_KEY_ID",
                                    "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"])
    if explicit.passed:
        return explicit
    hit = next((p for p in _IMPLICIT_CRED_CHAIN if re.search(p, text, re.IGNORECASE)), None)
    if hit:
        return Check("reads_env_creds", True, 1.0, axis="platform",
                     detail=f"chaine d'identifiants implicite (env) : {hit}")
    return explicit


def _tables(paths: list[Path]) -> list[tuple[Path, object, float]]:
    """(chemin, DataFrame, facteur de format) ; le CSV ne vaut qu'un demi-credit, le prompt
    demande du Parquet."""
    import pandas as pd
    out = []
    for p in sorted(paths):
        if not p.is_file():
            continue
        try:
            if p.suffix == ".parquet":
                out.append((p, pd.read_parquet(p), 1.0))
            elif p.suffix in (".csv", ".txt"):
                out.append((p, pd.read_csv(p), 0.5))
        except Exception:
            continue
    return out


def _find_outputs(out_dir: Path, ws: Path) -> tuple[list[Path], str]:
    """Sorties ecrites par le script : d'abord OUTPUT_URI, sinon n'importe ou dans le workspace
    (un script peut construire un chemin relatif) - hors donnees source et hors caches."""
    found = [p for p in out_dir.rglob("*") if p.is_file()]
    if found:
        return found, str(out_dir)
    extra = []
    for p in ws.rglob("*"):
        if not p.is_file() or p.suffix not in (".parquet", ".csv"):
            continue
        rel = _rel(ws, p)
        if rel.parts and rel.parts[0] in (".git", ".venv", ".opencode"):
            continue
        if "census" in rel.as_posix() or rel.name == "donnees_insee.csv":
            continue  # donnees source, pas une sortie
        extra.append(p)
    return extra, f"{out_dir} (vide) puis le workspace"


def _score_outputs(out_dir: Path, ws: Path) -> list[Check]:
    paths, where = _find_outputs(out_dir, ws)
    tables = _tables(paths)
    if not tables:
        d = f"aucun fichier Parquet/CSV trouve (cherche dans : {where})"
        return [Check("median_by_departement_correct", False, 0.0, weight=2.0, detail=d),
                Check("top10_correct", False, 0.0, weight=2.0, detail=d)]
    exp_med = EXPECTED["median_revenu_by_departement"]
    exp_top = [t["commune"] for t in EXPECTED["top10_communes"]]
    best_med = (0.0, "aucune table (departement, mediane) reconnue")
    best_top = (0.0, "aucune table top 10 reconnue")
    for p, df, fmt in tables:
        # mediane par departement : essayer chaque paire (colonne clef, colonne numerique)
        for dep_col in df.columns:
            keys = [str(k).strip() for k in df[dep_col]]
            if not set(keys) & set(exp_med):
                continue
            for num_col in df.columns:
                if num_col == dep_col:
                    continue
                try:
                    got = {k: float(v) for k, v in zip(keys, df[num_col])}
                except (TypeError, ValueError):
                    continue
                ok = sum(1 for k, v in exp_med.items() if k in got and abs(got[k] - v) <= 0.6)
                frac = ok / len(exp_med) * fmt
                if frac > best_med[0]:
                    best_med = (frac, f"{p.name}: {ok}/{len(exp_med)} medianes exactes "
                                      f"({dep_col} x {num_col})")
        # top 10 : essayer TOUTES les colonnes, pas la premiere qui contient "commune"
        # (`code_commune` arrivait avant `commune` et donnait une intersection vide - 4/4
        # cellules a 0 alors que 3 avaient un top 10 parfait).
        if len(df) <= 12:
            for col in df.columns:
                got = {str(x).strip() for x in df[col]}
                inter = len(got & set(exp_top))
                frac = inter / len(exp_top) * fmt
                if frac > best_top[0]:
                    best_top = (frac, f"{p.name}: {inter}/{len(exp_top)} communes du top 10 ({col})")
    return [Check("median_by_departement_correct", best_med[0] >= 0.99, best_med[0],
                  weight=2.0, detail=best_med[1]),
            Check("top10_correct", best_top[0] >= 0.99, best_top[0], weight=2.0,
                  detail=best_top[1])]


def grade(ctx):
    ws = ctx.workspace
    checks = []
    scripts = deliverable_files(ws, ["*.py"])
    checks.append(file_exists(ws, ["*.py"], name="script_present", axis="functional"))
    text = code_text(ws, ["*.py"])
    checks.append(references_s3(text))                 # lit s3:// + moteur en memoire
    checks.append(no_download_to_disk(text))           # pas de download_file/fs.get
    checks.append(_s3_credentials_ok(text))
    checks.append(env_vars_used(text, ["CENSUS_URI", "OUTPUT_URI"], name="uses_uri_env",
                                axis="functional"))
    checks.append(code_contains(text, [r"to_parquet|write_parquet|COPY\s|write_table|sink_parquet"],
                                name="writes_parquet", axis="functional"))
    checks.append(no_hardcoded_secrets(text))

    if not scripts:
        checks.append(Check("script_runs", False, 0.0, detail="aucun script"))
        checks += [Check("median_by_departement_correct", False, 0.0, weight=2.0, detail="aucun script"),
                   Check("top10_correct", False, 0.0, weight=2.0, detail="aucun script")]
        return checks

    # Execution sur le miroir local, sortie dans un dossier hors workspace (le workspace est
    # note et uploade tel quel).
    out_dir = ws.parent / "grade_out_t01"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    script = _pick_entry_script(ws, scripts)
    env_backup = {k: os.environ.get(k) for k in ("CENSUS_URI", "OUTPUT_URI")}
    os.environ["CENSUS_URI"] = "data/census"
    os.environ["OUTPUT_URI"] = str(out_dir)
    try:
        r, note = run_in_project(ws, ["python", str(_rel(ws, script))], timeout=300)
        ok = r.returncode == 0
        checks.append(Check("script_runs", ok, 1.0 if ok else 0.0,
                            detail=f"{_rel(ws, script)} [{note}] {_tail(r)}"))
    except Exception as e:
        checks.append(Check("script_runs", False, 0.0, detail=f"erreur: {e}"))
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    checks.extend(_score_outputs(out_dir, ws))
    return checks
