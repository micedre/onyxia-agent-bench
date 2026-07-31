"""Rapport de benchmark lisible par un humain (Markdown, sans dependance).

Assemble a partir du meme `summary` que `summary.json` (voir `bench/runner.py:aggregate`),
plus quelques metadonnees d'invocation (`meta`). Ecrit localement (`runs/<run>/report.md`) et
loggue comme artefact MLflow sur le run parent (`bench/mlflow_logging.py:log_summary`).
"""
from __future__ import annotations

from bench.schema import AXES

AXIS_LABELS = {
    "functional": "La tache produite fonctionne (fichier attendu, tests, bonne reponse).",
    "platform": "Conventions de la plateforme respectees (S3 en memoire, creds via env, etc.).",
    "repro": "Reproductibilite (lockfile, versions figees, sortie stable en re-execution).",
    "safety": "Aucune fuite de secret ni action dangereuse.",
    "efficiency": "Cout en tokens/temps/tentatives pour arriver au resultat.",
}

COLUMNS = AXES + ["combined"]


def _fmt(v) -> str:
    return f"{v:.2f}" if isinstance(v, (int, float)) else "–"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def _cell_header(rec: dict) -> str:
    status = "OK"
    if rec.get("error"):
        status = f"ECHEC ({rec['error']})"
    scores = " ".join(f"{a}={_fmt(rec['axis_scores'].get(a))}" for a in COLUMNS
                      if rec["axis_scores"].get(a) is not None)
    extra = f", steps_to_diagnosis={rec['metrics']['steps_to_diagnosis']}" \
        if "steps_to_diagnosis" in rec.get("metrics", {}) else ""
    return (f"### {rec['task']} / {rec['config']} / seed{rec['seed']} — {status}\n\n"
            f"- Scores : {scores}\n"
            f"- tokens={rec.get('tokens_total', 0)} · tool_calls={rec.get('tool_calls', 0)} "
            f"· wall_clock={rec.get('wall_clock_s', 0):.1f}s{extra}")


def _cell_checks(rec: dict) -> str:
    checks = rec.get("checks") or []
    if not checks:
        return ""
    lines = ["", "- Checks :"]
    for c in checks:
        mark = "✅" if c["passed"] else "❌"
        lines.append(f"  - {mark} `{c['name']}` ({c['axis']}, {_fmt(c['score'])}) — {c['detail']}")
    return "\n".join(lines)


def render_markdown(summary: dict, meta: dict) -> str:
    mean_by_config = summary.get("mean_by_config", {})
    delta = summary.get("delta_by_axis", {})
    compared = summary.get("compared", {})
    n_cells = summary.get("n_cells", 0)
    records = summary.get("records", [])

    out = [f"# Rapport de benchmark — {meta.get('run_name', '?')}", ""]
    out.append(f"Modele : `{meta.get('model', '?')}` · Seeds : {meta.get('seeds', '?')} · "
               f"Taches : {', '.join(meta.get('tasks', []))} · "
               f"Configs : {', '.join(meta.get('configs', []))} · {n_cells} cellules")
    out.append("")

    out.append("## Ce que mesure chaque axe")
    out.append("")
    for a in AXES:
        out.append(f"- **{a}** — {AXIS_LABELS[a]}")
    out.append("- **combined** — moyenne non ponderee des axes ci-dessus effectivement "
               "mesures pour la tache (pas de ponderation metier, pas de garde-fou securite).")
    out.append("")

    out.append("## Resume par config")
    out.append("")
    headers = ["config"] + COLUMNS
    rows = [[cfg] + [_fmt(axes.get(a)) for a in COLUMNS] for cfg, axes in mean_by_config.items()]
    out.append(_table(headers, rows))
    out.append("")

    if delta:
        low, high = compared.get("low", "?"), compared.get("high", "?")
        out.append(f"## Delta {high} − {low} (l'apport du contexte)")
        out.append("")
        out.append(_table(["axe", "delta"],
                          [[a, f"{delta[a]:+.2f}"] for a in COLUMNS if a in delta]))
        out.append("")
        if n_cells < 20:
            out.append(f"> ⚠️ Echantillon restreint ({n_cells} cellules) : ce delta valide que "
                       "le harnais fonctionne, ce n'est pas encore un signal statistiquement "
                       "significatif. Voir le plan de benchmark pour la taille d'echantillon "
                       "recommandee avant d'en tirer une conclusion.")
            out.append("")

    out.append("## Detail par cellule")
    out.append("")
    for rec in records:
        out.append(_cell_header(rec))
        checks_md = _cell_checks(rec)
        if checks_md:
            out.append(checks_md)
        out.append("")

    out.append("## Limites connues")
    out.append("")
    out.append("- Les axes `platform` et `repro` mesurent la conformite aux conventions "
               "*definies par ce projet*, pas une verite externe : un bon score dit "
               "\"suit nos regles\", pas \"objectivement correct\".")
    out.append("- Le score `combined` pondere tous les axes mesures a egalite ; un mauvais "
               "score `safety` peut donc etre compense par de bons scores ailleurs.")
    out.append("- Les axes sans check pour une tache (ex. `repro`/`efficiency` sur T01/T10) "
               "n'entrent pas dans la moyenne combinee de cette tache.")

    return "\n".join(out) + "\n"
