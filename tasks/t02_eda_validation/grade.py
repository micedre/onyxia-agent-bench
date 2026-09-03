"""Notation de T02 : le script produit fait de l'EDA + une validation explicite, ET detecte
effectivement les deux pieges du fichier (revenu manquant a Marseille, population negative a
Nice) - c'est la sortie du script qui est jugee, pas seulement des motifs dans le code."""
from __future__ import annotations

import re

from bench.grading import (
    Check,
    _rel,
    _tail,
    code_contains,
    code_text,
    deliverable_files,
    file_exists,
    run_python_script,
)

EDA = [r"\.describe\(", r"\.isna\(", r"\.isnull\(", r"value_counts\(", r"null_count\(",
       r"\bSUMMARIZE\b", r"\.dtypes\b", r"\.info\(", r"\.schema\b", r"\.summary\(",
       r"\.quantile\(", r"\.hist\(", r"describe\("]
VALIDATION = [r"\bassert\b", r"raise\s+\w*(Error|Exception)", r"pandera", r"pointblank",
              r"great_expectations", r"\.between\(", r"validat", r"plausib", r"hors[_ ]plage",
              r"out[_ ]of[_ ]range", r"[<>]=?\s*0\b", r"\.clip\(", r"check_", r"is_valid",
              r"anomal", r"invalid"]
MISSING_RE = re.compile(r"(?is)(revenu\w*.{0,120}?(manquant|missing|null|nan|\bna\b|absent|vide)"
                        r"|(manquant|missing|null|nan|\bna\b|absent|vide).{0,120}?revenu)")
NEGATIVE_RE = re.compile(r"(?is)(population.{0,120}?(negati|invalid|hors.?plage|plausib|out.of.range"
                         r"|<\s*=?\s*0|-100|anomal|aberrant|incoherent)"
                         r"|(negati|invalid|hors.?plage|plausib|-100|anomal|aberrant|incoherent)"
                         r".{0,120}?population)")


def grade(ctx):
    ws = ctx.workspace
    checks = []
    checks.append(file_exists(ws, ["*.py"], name="script_present"))
    text = code_text(ws, ["*.py"])
    checks.append(code_contains(text, EDA, name="eda_calls_present"))
    checks.append(code_contains(text, VALIDATION, name="validation_step_present"))

    script, r, note = run_python_script(ws, ["*.py"])
    if script is None:
        checks.append(Check("script_runs", False, 0.0, detail="aucun script"))
        checks.append(Check("traps_detected", False, 0.0, weight=2.0, detail="aucun script"))
        return checks
    if r is None:
        checks.append(Check("script_runs", False, 0.0, detail=f"{_rel(ws, script)}: {note}"))
        checks.append(Check("traps_detected", False, 0.0, weight=2.0, detail=note))
        return checks
    # Un script qui s'arrete volontairement sur donnees invalides (exit != 0 apres avoir
    # signale le probleme) est un comportement legitime : on ne penalise que les plantages.
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    crashed = r.returncode != 0 and "Traceback" in out
    checks.append(Check("script_runs", not crashed, 0.0 if crashed else 1.0,
                        detail=f"{_rel(ws, script)} [{note}] rc={r.returncode} {_tail(r)}"))
    # rapports ecrits par le script (json/md/txt) : comptent aussi comme signalement
    for p in deliverable_files(ws, ["*.json", "*.md", "*.txt"]):
        if re.search(r"(?i)valid|rapport|report|qualit", p.name):
            try:
                out += "\n" + p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    found_missing = bool(MISSING_RE.search(out))
    found_negative = bool(NEGATIVE_RE.search(out))
    s = (found_missing + found_negative) / 2
    checks.append(Check("traps_detected", s >= 0.99, s, weight=2.0,
                        detail=f"revenu manquant signale={found_missing}, "
                               f"population negative signalee={found_negative}"))
    return checks
