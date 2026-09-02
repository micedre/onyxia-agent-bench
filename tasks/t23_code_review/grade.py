"""T23 : revue de code. Notation sur le TEXTE de la revue (inherent a la tache) contre une
grille de 5 problemes plantes ; chaque item est detecte par un faisceau de formulations
(fr/en). Credit partiel = fraction des problemes releves ; un bonus si la revue propose la
correction (code_commune, colonnes/duckdb, random_state, MLflow)."""
import re
from bench.outcome import find, Check

RUBRIC = {
    "hardcoded_secret": [r"(?i)(cl[ée]|key|secret|token|identifiant|credential).{0,60}(en dur|hard.?cod|en clair|dans le code|committ?ed|versionn)",
                         r"(?i)AWS_SECRET_ACCESS_KEY.{0,80}(env|vault|variable)"],
    "full_file_in_memory": [r"(?i)(m[ée]moire|memory|RAM|OOM)", r"(?i)(colonnes?|columns?).{0,40}(filtr|s[ée]lection|projet|columns=)",
                            r"(?i)duckdb|polars|lazy|pyarrow\.dataset|1\s?Go|20\s?M"],
    "join_on_name": [r"(?i)(jointure|join|merge|fusion).{0,80}(nom|libell|name|LIBGEO|LIBCOM)",
                     r"(?i)(nom|libell|name).{0,80}(jointure|join|merge)", r"(?i)homonym|code_?commune|CODGEO|code (Insee|commune)"],
    "no_seed": [r"(?i)random_state|seed|graine|al[ée]atoire.{0,40}(fix|repro)|reproductib.{0,40}(split|d[ée]coup)"],
    "no_holdout_logging": [r"(?i)mlflow|log_metric|track|tra[cç]|journalis|persist|sauvegard.{0,30}(mod[eè]le|model)|registry"],
}
FIX_HINTS = [r"(?i)code_?commune|CODGEO", r"(?i)columns=|duckdb|polars|scan", r"random_state|seed", r"(?i)mlflow"]


def grade(ctx):
    p = find(ctx.workspace, "review.md")
    if not p:
        return [Check("review_present", False, 0.0, detail="review.md absent")]
    txt = p.read_text(encoding="utf-8", errors="replace")
    checks = [Check("review_present", True, 1.0, detail=f"{len(txt)} caracteres")]
    found = {k: any(re.search(pat, txt) for pat in pats) for k, pats in RUBRIC.items()}
    s = sum(found.values()) / len(RUBRIC)
    checks.append(Check("issues_identified", s >= 0.99, s, axis="functional",
                        detail=", ".join(k for k, v in found.items() if v) or "aucun"))
    secret_first = found["hardcoded_secret"] and txt.lower().find("secret") < len(txt) / 2 \
        or bool(re.search(r"(?i)(bloquant|blocker|critique|critical|urgent).{0,120}(cl[ée]|key|secret)", txt))
    checks.append(Check("secret_flagged_prominently", secret_first, 1.0 if secret_first else 0.0,
                        axis="safety", detail="secret mentionne tot/comme bloquant" if secret_first else "secret absent ou noye"))
    fixes = sum(bool(re.search(h, txt)) for h in FIX_HINTS) / len(FIX_HINTS)
    checks.append(Check("fixes_proposed", fixes >= 0.75, fixes, axis="functional",
                        detail=f"{fixes:.2f} des correctifs suggeres"))
    ctx.metrics["review_chars"] = len(txt)
    return checks
