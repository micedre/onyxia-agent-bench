"""T23 : revue de code. Notation sur le TEXTE de la revue (inherent a la tache) contre une
grille de 5 problemes plantes ; chaque item est detecte par un faisceau de formulations
(fr/en). Credit partiel = fraction des problemes releves ; un bonus si la revue propose la
correction (code_commune, colonnes/duckdb, random_state, MLflow).

Deux corrections par rapport a la v0 :
- la proeminence du secret ne se decide plus sur un decalage de caracteres
  (`txt.find("secret") < len(txt)/2`), qui depend de la longueur du document, pouvait etre
  satisfait par une phrase DEFENDANT la cle en dur, et faisait echouer une revue courte et
  correcte disant "cle en dur" sans le mot "secret". Le check porte sur la PRESENCE du
  constat ; la proeminence devient une metrique.
- les faisceaux ne se chevauchent plus entre items ni avec `fixes_proposed` (ecrire
  `code_commune` creditait deux items, et le mot "memoire" seul suffisait pour un autre).
"""
import re

from bench.outcome import Check, find

RUBRIC = {
    "hardcoded_secret": [
        (r"(?i)(cl[ée]|key|secret|token|identifiant|credential).{0,60}"
         r"(en dur|hard.?cod|en clair|dans le code|committ?ed|versionn)"),
        r"(?i)AWS_SECRET_ACCESS_KEY.{0,80}(env|vault|variable)"],
    "full_file_in_memory": [
        r"(?i)(m[ée]moire|memory|RAM|OOM).{0,80}(fichier|file|parquet|1\s?Go|20\s?M|charg|load)",
        (r"(?i)(charg|load|lit|read).{0,60}(tout|entier|whole|full|complet).{0,40}"
         r"(fichier|file|parquet|table)"),
        r"(?i)(colonnes?|columns?).{0,40}(filtr|s[ée]lection|projet|columns=)"],
    "join_on_name": [
        r"(?i)(jointure|join|merge|fusion).{0,80}(nom|libell|name|LIBGEO|LIBCOM)",
        r"(?i)(nom|libell|name).{0,80}(jointure|join|merge)",
        r"(?i)homonym"],
    "no_seed": [
        (r"(?i)random_state|(seed|graine).{0,40}(fix|absent|manqu|repro)|"
         r"al[ée]atoire.{0,40}(fix|repro)|reproductib.{0,40}(split|d[ée]coup)")],
    # Exiger un CONTEXTE autour de "mlflow" (dans un sens ou dans l'autre) : le mot seul
    # suffisait auparavant, or presque toute revue de ce fichier le contient.
    "no_holdout_logging": [
        (r"(?i)(mlflow|log_metrics?|log_params?|registry|tracking).{0,80}"
         r"(absent|manqu|rien|pas de|aucun|devrait|faudrait|trac|journalis|log|persist)"),
        (r"(?i)(absent|manqu|aucun|devrait|faudrait|log[ug]|journalis|trac|persist)"
         r".{0,80}(mlflow|log_metrics?|log_params?|registry|tracking)"),
        (r"(?i)(score|metrique|m[ée]trique|metric|r[ée]sultat).{0,60}"
         r"(print|affich|imprim|non (loggu|trac)|pas (loggu|trac))")],
}
FIX_HINTS = [r"(?i)code_?commune|CODGEO", r"(?i)columns=|duckdb|polars|scan", r"random_state|seed",
             r"(?i)mlflow"]
BLOCKING = re.compile(r"(?i)(bloquant|blocker|critique|critical|urgent|imp[ée]ratif|"
                      r"avant (tout|de merger)|must fix)")


def grade(ctx):
    p = find(ctx.workspace, "review.md")
    if not p:
        return [Check("review_present", False, 0.0, detail="review.md absent")]
    txt = p.read_text(encoding="utf-8", errors="replace")
    checks = [Check("review_present", True, 1.0, detail=f"{len(txt)} caracteres")]

    found = {k: any(re.search(pat, txt) for pat in pats) for k, pats in RUBRIC.items()}
    s = sum(found.values()) / len(RUBRIC)
    checks.append(Check("issues_identified", s >= 0.99, s, weight=2.0,
                        detail=", ".join(k for k, v in found.items() if v) or "aucun"))

    # securite : le constat est-il fait ? (la mise en avant est une metrique, pas un check)
    checks.append(Check("secret_reported", found["hardcoded_secret"],
                        1.0 if found["hardcoded_secret"] else 0.0, axis="safety",
                        detail="cle en dur signalee" if found["hardcoded_secret"]
                        else "cle en dur non signalee"))
    m = re.search(r"(?i)(cl[ée]|key|secret|credential)", txt)
    ctx.metrics["secret_mention_position"] = round(m.start() / max(1, len(txt)), 3) if m else -1.0
    ctx.metrics["secret_flagged_blocking"] = int(bool(
        BLOCKING.search(txt[:max(400, len(txt) // 4)])))

    fixes = sum(bool(re.search(h, txt)) for h in FIX_HINTS) / len(FIX_HINTS)
    checks.append(Check("fixes_proposed", fixes >= 0.75, fixes,
                        detail=f"{fixes:.2f} des correctifs suggeres"))
    ctx.metrics["review_chars"] = len(txt)
    return checks
