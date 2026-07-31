"""Notation offline de T11 : le graphique (chart.png, genere avec des valeurs connues a
l'avance - voir fixtures/) contient un maximum (Paris, 2148) et un minimum (Nice, 343)
verifiables. Ne teste un signal reel qu'avec un modele capable de vision (ex.
`onyxia/qwen3-vl`) - un modele texte seul n'a aucune chance de lire chart.png, voir le
README pour cette limite connue."""
from bench.grading import code_contains, code_text, file_exists


def grade(ctx):
    checks = []
    checks.append(file_exists(ctx.workspace, ["resume.md"], name="resume_present"))
    text = code_text(ctx.workspace, ["resume.md"])
    checks.append(code_contains(text, [r"paris"], name="max_commune_correcte"))
    checks.append(code_contains(text, [r"2148", r"2\s*148", r"2[.,]1\s*million"],
                                name="max_valeur_correcte"))
    return checks
