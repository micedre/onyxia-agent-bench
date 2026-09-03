"""Notation de T02 : on juge le RESULTAT, pas les mots-cles du code.

v0 cherchait `.describe(` / `assert` / `< ?0` dans le source : satisfaisable par un
commentaire, et les couches de contexte contiennent elles-memes ces mots-cles (une config
haute pouvait donc scorer en recopiant sa doc). Ici :

1. les livrables sont SUPPRIMES puis le script de l'agent est REEXECUTE dans le workspace
   (axe repro : "ca retourne tel quel") ;
2. `validation_report.json` est compare a `ground_truth.json` (nb de lignes, manquants par
   colonne, aberrants par colonne - credit partiel selon la fraction d'anomalies plantees
   detectees) ;
3. `revenu_median_departement.csv` est compare departement par departement (credit partiel =
   fraction des departements a +/-1 %) ; check dedie pour les codes `01`..`09` / `2A` / `2B`,
   le piege classique du zero en tete perdu.

Les cles JSON / noms de colonnes sont reconnus de facon tolerante (fr/en, snake/camel) :
on note la validation, pas la conformite a un schema que le prompt n'impose pas.
"""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

from bench.grading import Check, code_text, no_hardcoded_secrets
from bench.outcome import find, reexecute

TRUTH = json.loads((Path(__file__).parent / "ground_truth.json").read_text(encoding="utf-8"))
REPORT, MEDIANS = "validation_report.json", "revenu_median_departement.csv"
NUM_COLS = ["population", "revenu_disponible_median"]

_ROWS_KEYS = re.compile(r"(?i)^(n_?rows?|rows?|nb?_?lignes?|lignes?|n_?lines?|row_?count|"
                        r"n_?obs|n|total|count|nombre_de_lignes)$")
_MISSING_KEYS = re.compile(r"(?i)(missing|manquant|null|\bna\b|nan|vide|absent)")
_OUTLIER_KEYS = re.compile(r"(?i)(aberrant|outlier|out_?of_?range|hors_?plage|invalid|"
                           r"implausible|anomal|suspect)")


# ---------------------------------------------------------------- execution
#
# La reexecution passe par `bench.outcome.reexecute` : elle copie le workspace, y supprime les
# livrables et relance le script dans l'environnement du PROJET de l'agent (`uv run`). La
# version initiale de ce grader avait sa propre mecanique inline, qui (a) supprimait les
# livrables DANS le workspace note - une reexecution en echec detruisait donc la preuve et la
# notation n'etait plus idempotente -, (b) lancait `python` du harnais et non l'environnement
# de l'agent, et (c) passait un chemin relatif au sous-processus.


# ---------------------------------------------------------------- rapport JSON


def _walk(o, path=()):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _walk(v, path + (str(k),))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _walk(v, path + (str(i),))
    else:
        yield path, o


def _as_int(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str) and re.fullmatch(r"\s*\d+\s*", v):
        return int(v)
    return None


def _per_column_counts(report, key_re: re.Pattern) -> dict[str, int]:
    """Pour chaque colonne numerique, le compteur entier situe sous une cle qui matche
    `key_re` et dont le chemin mentionne la colonne, dans n'importe quel ordre :
    {"missing": {"population": 3}}, {"population": {"n_missing": 3}}, ou la forme liste
    [{"column": "population", "missing": 3}]."""
    out: dict[str, int] = {}
    leaves = list(_walk(report))
    for col in NUM_COLS:
        col_re = re.compile(rf"(?i)\b{col}\b|\b{col.split('_')[0]}\b")
        for path, v in leaves:
            joined = "/".join(path)
            n = _as_int(v)
            if n is not None and key_re.search(joined) and col_re.search(joined):
                out[col] = n
                break
        if col in out:
            continue
        for path, v in leaves:  # forme liste d'objets : fratrie {"column": col, "<key>": n}
            if isinstance(v, str) and col_re.search(v) and len(path) >= 2:
                parent = path[:-1]
                for p2, v2 in leaves:
                    if p2[:-1] == parent and key_re.search(p2[-1]) and _as_int(v2) is not None:
                        out[col] = _as_int(v2)
                        break
            if col in out:
                break
    return out


def _rows_count(report) -> int | None:
    for path, v in _walk(report):
        if path and _ROWS_KEYS.match(path[-1]) and _as_int(v) is not None:
            return _as_int(v)
    return None


def _grade_report(ws: Path) -> list[Check]:
    p = find(ws, REPORT)
    if not p:
        return [Check("report_present", False, 0.0, detail=f"{REPORT} absent")]
    try:
        report = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        return [Check("report_present", False, 0.0, detail=f"JSON invalide: {e}")]
    checks = [Check("report_present", True, 1.0, detail=str(p.relative_to(ws)))]

    n = _rows_count(report)
    ok = n == TRUTH["n_rows"]
    checks.append(Check("report_row_count", ok, 1.0 if ok else 0.0,
                        detail=f"lu={n}, attendu={TRUTH['n_rows']}"))

    # manquants : blancs stricts acceptes, ou blancs + placeholders (`s`, `nd`) si l'agent
    # les a legitimement traites comme manquants.
    got = _per_column_counts(report, _MISSING_KEYS)
    score, det = 0.0, []
    for col in NUM_COLS:
        lo = TRUTH["missing"][col]
        hi = lo + TRUTH["placeholders"][col]
        v = got.get(col)
        hit = v is not None and lo <= v <= hi
        score += 0.5 if hit else 0.0
        det.append(f"{col}: lu={v}, attendu dans [{lo},{hi}]")
    checks.append(Check("report_missing_counts", score >= 1.0, score, detail="; ".join(det)))

    # aberrants : credit partiel = fraction des anomalies plantees detectees, avec une marge
    # pour une regle plus stricte que la notre (ex. population > 1M), mais pas un filet trop
    # large (signaler 200 lignes n'est pas "detecter" 7 anomalies).
    got = _per_column_counts(report, _OUTLIER_KEYS)
    score, det = 0.0, []
    for col in NUM_COLS:
        planted = TRUTH["out_of_range"][col]
        ceiling = 2 * planted + 6
        v = got.get(col)
        if v is None or v <= 0:
            s = 0.0
        elif planted <= v <= ceiling:
            s = 1.0
        elif v < planted:
            s = v / planted
        else:
            s = 0.5
        score += s / len(NUM_COLS)
        det.append(f"{col}: lu={v}, plantes={planted}")
    checks.append(Check("report_outlier_counts", score >= 0.99, score, detail="; ".join(det)))
    return checks


# ---------------------------------------------------------------- medianes CSV


def _read_medians(p: Path) -> dict[str, float]:
    raw = p.read_bytes()
    text = None
    for enc in ("utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        return {}
    try:
        dialect = csv.Sniffer().sniff(text[:2000], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    if not rows:
        return {}
    cols = [c for c in rows[0].keys() if c is not None]
    dep_col = next((c for c in cols if re.search(r"(?i)dep|dpt|dept", c)), cols[0])
    val_col = next((c for c in cols if c != dep_col
                    and re.search(r"(?i)median|revenu|income|value", c)), None)
    if val_col is None:
        val_col = next((c for c in cols if c != dep_col), None)
    out: dict[str, float] = {}
    for r in rows:
        d = (r.get(dep_col) or "").strip().strip('"')
        try:
            out[d] = float((r.get(val_col) or "").replace(",", ".").replace(" ", ""))
        except ValueError:
            continue
    return out


def _grade_medians(ws: Path) -> list[Check]:
    p = find(ws, MEDIANS)
    if not p:
        return [Check("medians_present", False, 0.0, detail=f"{MEDIANS} absent")]
    got = _read_medians(p)
    checks = [Check("medians_present", bool(got), 1.0 if got else 0.0,
                    detail=f"{len(got)} departement(s) lus")]
    truth = TRUTH["median_revenu_by_departement"]

    # codes preserves tels quels ("01", "2A") : le piege du zero en tete perdu
    fragile = TRUTH["departements_zero_en_tete"] + TRUTH["departements_corse"]
    kept = [d for d in fragile if d in got]
    lost = [d for d in fragile if d not in got and d.lstrip("0") in got]
    s = len(kept) / len(fragile)
    checks.append(Check("dept_codes_preserved", s >= 0.99, s, axis="functional",
                        detail=f"preserves={kept}, perdus={lost}"))

    # Exactitude : +/-50 EUR par departement, en ABSOLU (accepte un code sans zero, deja
    # penalise ci-dessus). Une tolerance de 1 % (~215 EUR) absorbait les consequences du
    # piege : un agent qui traite `s`/`nd` comme des zeros ou garde les revenus x100 decalait
    # la mediane sans sortir de la tolerance. 50 EUR laisse en revanche passer une divergence
    # d'une ligne sur le perimetre exclu, qui est un choix defendable.
    hits, misses = 0, []
    for d, ref in truth.items():
        v = got.get(d, got.get(d.lstrip("0")))
        if v is not None and abs(v - ref) <= 50.0:
            hits += 1
        else:
            misses.append(f"{d}:{v}!={ref}")
    s = hits / len(truth)
    checks.append(Check("medians_correct", s >= 0.99, s, axis="functional", weight=2.0,
                        detail=f"{hits}/{len(truth)} ok" + (f" ; ex. {misses[:3]}" if misses else "")))
    return checks


# ---------------------------------------------------------------- entree


def grade(ctx):
    ws = ctx.workspace
    chk, info = reexecute(ws, [REPORT, MEDIANS], timeout=180)
    rws = info["ws"]          # la copie si la reexecution a reussi, le workspace sinon
    checks = [chk]
    checks += _grade_report(rws)
    checks += _grade_medians(rws)
    checks.append(no_hardcoded_secrets(code_text(ws, ["*.py"])))
    ctx.metrics["t02_medians_ok_frac"] = next(
        (c.score for c in checks if c.name == "medians_correct"), 0.0)
    return checks
