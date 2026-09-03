"""Notation de T01 : le CODE suit les conventions S3 de la plateforme (statique) ET produit
les bons chiffres quand on l'execute sur le miroir local (CENSUS_URI=data/census).
La verite terrain est dans expected.json (hors fixtures, l'agent ne la voit pas)."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from bench.grading import (
    Check,
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


def _read_tables(out_dir: Path) -> list:
    import pandas as pd
    tables = []
    for p in sorted(out_dir.rglob("*")):
        if not p.is_file():
            continue
        try:
            if p.suffix == ".parquet":
                tables.append((p, pd.read_parquet(p), 1.0))
            elif p.suffix == ".csv":
                tables.append((p, pd.read_csv(p, dtype=str), 0.5))  # demi-credit : pas du Parquet
        except Exception:
            continue
    return tables


def _score_outputs(out_dir: Path) -> tuple[Check, Check]:
    tables = _read_tables(out_dir)
    if not tables:
        d = "aucun fichier Parquet/CSV ecrit dans OUTPUT_URI"
        return (Check("median_by_departement_correct", False, 0.0, detail=d),
                Check("top10_correct", False, 0.0, detail=d))
    exp_med = EXPECTED["median_revenu_by_departement"]
    exp_top = [t["commune"] for t in EXPECTED["top10_communes"]]
    best_med, best_top = (0.0, "pas de table departement/mediane"), (0.0, "pas de table top 10")
    for p, df, fmt in tables:
        cols = {c.lower(): c for c in df.columns}
        dep_col = next((cols[c] for c in cols if "dep" in c), None)
        num_cols = [c for c in df.columns if c != dep_col]
        if dep_col is not None and num_cols and len(df) >= len(exp_med) - 1:
            for nc in num_cols:
                try:
                    got = {str(k).strip(): float(v) for k, v in zip(df[dep_col], df[nc])}
                except (TypeError, ValueError):
                    continue
                ok = sum(1 for k, v in exp_med.items()
                         if k in got and abs(got[k] - v) <= 0.6)
                frac = ok / len(exp_med) * fmt
                if frac > best_med[0]:
                    best_med = (frac, f"{p.name}: {ok}/{len(exp_med)} medianes exactes ({nc})")
        name_col = next((cols[c] for c in cols if "commune" in c or "nom" in c or "name" in c), None)
        if name_col is not None and len(df) <= 12:
            got = {str(x).strip() for x in df[name_col]}
            inter = len(got & set(exp_top))
            frac = inter / len(exp_top) * fmt
            if frac > best_top[0]:
                best_top = (frac, f"{p.name}: {inter}/10 communes du top 10")
    return (Check("median_by_departement_correct", best_med[0] >= 0.99, best_med[0],
                  weight=2.0, detail=best_med[1]),
            Check("top10_correct", best_top[0] >= 0.99, best_top[0], weight=2.0,
                  detail=best_top[1]))


def grade(ctx):
    ws = ctx.workspace
    checks = []
    scripts = deliverable_files(ws, ["*.py"])
    checks.append(file_exists(ws, ["*.py"], name="script_present", axis="functional"))
    text = code_text(ws, ["*.py"])
    checks.append(references_s3(text))                 # lit s3:// + moteur en memoire
    checks.append(no_download_to_disk(text))           # pas de download_file/fs.get
    checks.append(env_vars_used(text, ["AWS_S3_ENDPOINT", "AWS_ACCESS_KEY_ID",
                                       "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"]))
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

    # Execution sur le miroir local, sortie dans un dossier hors workspace
    out_dir = ws.parent / "grade_out_t01"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    from bench.grading import _pick_entry_script, _rel, _tail  # helpers internes
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
    checks.extend(_score_outputs(out_dir))
    return checks
