"""T02 : le grader note le resultat. Trois solutions plausibles d'agent, en stdlib pour
rester hermetique (pas de pandas cote harnais) :

- `GOOD`  : lit en latin-1 / `;`, garde les codes en chaine, gere `s`/`nd`, borne les
            valeurs -> tout passe ;
- `NAIVE` : encodage/separateur par defaut -> plante ou ne produit rien ;
- `LOST0` : correcte sauf `int(code_departement)` -> medianes justes mais codes perdus.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TASK = REPO / "tasks" / "t02_eda_validation"
sys.path.insert(0, str(REPO / "scripts"))
import gen_insee_fixture as gen  # noqa: E402


def _load_grader():
    spec = importlib.util.spec_from_file_location("t02_grade", TASK / "grade.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Ctx:
    def __init__(self, ws: Path):
        self.workspace, self.transcript, self.files_changed, self.run = ws, None, [], None
        self.metrics: dict = {}


def _workspace(tmp_path: Path, script: str) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    shutil.copy2(TASK / "fixtures" / "donnees_insee.csv", ws / "donnees_insee.csv")
    (ws / "analyse.py").write_text(script, encoding="utf-8")
    subprocess.run(["git", "-C", str(ws), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(ws), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(ws), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "fixtures"], check=True)
    return ws


GOOD = r'''
import csv, json, statistics
POP = (1, 3_000_000); REV = (5_000, 100_000); NA = {"", "s", "nd"}
rows = list(csv.DictReader(open("donnees_insee.csv", encoding="latin-1"), delimiter=";"))
def num(v):
    v = v.strip()
    return None if v in NA else float(v)
missing = {c: 0 for c in ("population", "revenu_disponible_median")}
outliers = {c: 0 for c in missing}
by_dep = {}
for r in rows:
    dep = r["code_departement"].strip()
    for col, (lo, hi) in (("population", POP), ("revenu_disponible_median", REV)):
        v = num(r[col])
        if v is None:
            missing[col] += 1
        elif not (lo <= v <= hi):
            outliers[col] += 1
        elif col == "revenu_disponible_median":
            by_dep.setdefault(dep, []).append(v)
json.dump({"n_rows": len(rows), "missing_values": missing,
           "out_of_range": outliers, "rules": {"population": POP, "revenu": REV}},
          open("validation_report.json", "w"), indent=2)
with open("revenu_median_departement.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["code_departement", "revenu_median"])
    for dep in sorted(by_dep, key=str):
        w.writerow([dep, statistics.median(by_dep[dep])])
'''

NAIVE = r'''
import csv, json, statistics
rows = list(csv.DictReader(open("donnees_insee.csv")))   # utf-8 + virgule par defaut
by_dep = {}
for r in rows:
    by_dep.setdefault(r["code_departement"], []).append(float(r["revenu_disponible_median"]))
json.dump({"n_rows": len(rows)}, open("validation_report.json", "w"))
'''

LOST0 = GOOD.replace('dep = r["code_departement"].strip()',
                     'dep = int(r["code_departement"].strip()) if r["code_departement"].strip().isdigit() else r["code_departement"].strip()')


def _scores(ws: Path) -> dict[str, float]:
    ctx = Ctx(ws)
    return {c.name: c.score for c in _load_grader().grade(ctx)}


def test_reference_solution_passes_everything(tmp_path):
    s = _scores(_workspace(tmp_path, GOOD))
    assert s["script_reexecutes"] == 1.0
    assert s["report_row_count"] == 1.0
    assert s["report_missing_counts"] == 1.0
    assert s["report_outlier_counts"] == 1.0
    assert s["dept_codes_preserved"] == 1.0
    assert s["medians_correct"] == 1.0
    assert s["no_hardcoded_secret"] == 1.0


def test_naive_parsing_fails(tmp_path):
    s = _scores(_workspace(tmp_path, NAIVE))
    # plante a l'encodage (latin-1 lu en utf-8) ou ne produit pas les livrables
    assert s["script_reexecutes"] == 0.0
    assert s.get("medians_correct", 0.0) == 0.0


def test_leading_zero_loss_is_caught_but_medians_still_credited(tmp_path):
    s = _scores(_workspace(tmp_path, LOST0))
    assert s["script_reexecutes"] == 1.0
    assert s["medians_correct"] == 1.0          # les valeurs sont justes...
    assert s["dept_codes_preserved"] < 1.0      # ...mais "01" est devenu "1"
    assert s["dept_codes_preserved"] == pytest.approx(2 / 7)  # 2A/2B gardes, 01..09 perdus


def test_outlier_partial_credit_and_key_tolerance(tmp_path):
    ws = _workspace(tmp_path, GOOD)
    g = _load_grader()
    g.grade(Ctx(ws))
    truth = json.loads((TASK / "ground_truth.json").read_text())
    planted = truth["out_of_range"]["population"]
    # rapport en anglais/camelCase, forme liste, avec la moitie des aberrants trouves
    (ws / "validation_report.json").write_text(json.dumps({
        "rowCount": truth["n_rows"],
        "columns": [
            {"name": "population", "nullCount": truth["missing"]["population"],
             "outlierCount": planted // 2},
            {"name": "revenu_disponible_median",
             "nullCount": truth["missing"]["revenu_disponible_median"],
             "outlierCount": truth["out_of_range"]["revenu_disponible_median"]},
        ]}))
    checks = {c.name: c for c in g._grade_report(ws)}
    assert checks["report_row_count"].passed
    assert checks["report_missing_counts"].passed
    assert 0.0 < checks["report_outlier_counts"].score < 1.0


def test_generator_is_deterministic_and_matches_committed_truth():
    rows, truth = gen.generate(1200, 42)
    committed = json.loads((TASK / "ground_truth.json").read_text(encoding="utf-8"))
    assert truth["median_revenu_by_departement"] == committed["median_revenu_by_departement"]
    assert truth["out_of_range"] == committed["out_of_range"]
    assert len(rows) == committed["n_rows"]
