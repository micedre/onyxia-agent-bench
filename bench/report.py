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
    "efficiency": "Cout en tokens/temps par rapport au budget de la tache (1 = gratuit, 0 = budget epuise).",
}

COLUMNS = AXES + ["combined"]
STATUS_ORDER = ["ok", "timeout", "never_ran", "oom", "error"]


def _fmt(v) -> str:
    return f"{v:.2f}" if isinstance(v, (int, float)) else "–"


def _fmt_int(v) -> str:
    return f"{v:,.0f}".replace(",", " ") if isinstance(v, (int, float)) else "–"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def _short_error(err: str | None) -> str:
    if not err:
        return ""
    err = err.replace("`", "'").replace("\n", " ")
    return err if len(err) <= 120 else err[:117] + "..."


def _cell_header(rec: dict) -> str:
    status = rec.get("status", "ok" if not rec.get("error") else "error")
    label = "OK" if status == "ok" else status.upper()
    if rec.get("error"):
        label += f" — {_short_error(rec['error'])}"
    scores = " ".join(f"{a}={_fmt(rec['axis_scores'].get(a))}" for a in COLUMNS
                      if rec["axis_scores"].get(a) is not None)
    extra = f", steps_to_diagnosis={rec['metrics']['steps_to_diagnosis']}" \
        if "steps_to_diagnosis" in rec.get("metrics", {}) else ""
    excl = "" if status in ("ok", "timeout") else " (exclue des moyennes)"
    return (f"### {rec['task']} / {rec['config']} / seed{rec['seed']} — {label}{excl}\n\n"
            f"- Scores : {scores or '–'}\n"
            f"- tokens={_fmt_int(rec.get('tokens_total', 0))} · tours={rec.get('assistant_turns', 0)} "
            f"· tool_calls={rec.get('tool_calls', 0)} · rejets_permission={rec.get('permission_rejections', 0)} "
            f"· wall_clock={rec.get('wall_clock_s', 0):.1f}s{extra}")


def _cell_checks(rec: dict) -> str:
    checks = rec.get("checks") or []
    if not checks:
        return ""
    lines = ["", "- Checks :"]
    for c in checks:
        mark = "✅" if c["passed"] else "❌"
        if c["axis"] == "skipped":
            mark = "➖"
        detail = str(c["detail"]).replace("\n", " ")
        lines.append(f"  - {mark} `{c['name']}` ({c['axis']}, {_fmt(c['score'])}) — {detail}")
    return "\n".join(lines)


def render_markdown(summary: dict, meta: dict) -> str:
    mean_by_config = summary.get("mean_by_config", {})
    delta = summary.get("delta_by_axis", {})
    delta_paired = summary.get("delta_combined_paired")
    ci = summary.get("ci_by_config", {})
    reliability = summary.get("reliability", {})
    compared = summary.get("compared", {})
    n_cells = summary.get("n_cells", 0)
    n_valid = summary.get("n_valid", n_cells)
    records = summary.get("records", [])

    out = [f"# Rapport de benchmark — {meta.get('run_name', '?')}", ""]
    out.append(f"Modele : `{meta.get('model', '?')}` · Seeds : {meta.get('seeds', '?')} · "
               f"Taches : {', '.join(meta.get('tasks', []))} · "
               f"Configs : {', '.join(meta.get('configs', []))} · {n_cells} cellules "
               f"({n_valid} valides)")
    extra_meta = {k: v for k, v in meta.items()
                  if k not in ("run_name", "model", "seeds", "tasks", "configs")}
    if extra_meta:
        out.append("")
        out.append("Invocation : " + " · ".join(f"{k}=`{v}`" for k, v in extra_meta.items()))
    out.append("")

    out.append("## Ce que mesure chaque axe")
    out.append("")
    for a in AXES:
        out.append(f"- **{a}** — {AXIS_LABELS[a]}")
    out.append("- **combined** — moyenne non ponderee des axes ci-dessus effectivement "
               "mesures (pas de ponderation metier, pas de garde-fou securite).")
    out.append("")

    out.append("## Fiabilite du run (a lire AVANT les scores)")
    out.append("")
    out.append("Seules les cellules `ok` et `timeout` (l'agent a tourne) entrent dans les "
               "moyennes. `never_ran`/`oom`/`error` sont des defaillances d'infrastructure ou "
               "du harnais, comptees ici et exclues des scores.")
    out.append("")
    if reliability:
        headers = ["config", "cellules", "valides"] + STATUS_ORDER + ["taux timeout"]
        rows = []
        for cfg, rel in reliability.items():
            counts = rel.get("status_counts", {})
            rows.append([cfg, str(rel["n_cells"]), str(rel["n_valid"])]
                        + [str(counts.get(s, 0)) for s in STATUS_ORDER]
                        + [_fmt(rel.get("timeout_rate"))])
        out.append(_table(headers, rows))
        out.append("")
        lost = sum(rel["n_cells"] - rel["n_valid"] for rel in reliability.values())
        if lost:
            out.append(f"> ⚠️ {lost} cellule(s) non valides sur {n_cells} : voir "
                       "`k8s_failure.txt` dans le dossier des cellules concernees.")
            out.append("")

    out.append("## Resume par config (cellules valides)")
    out.append("")
    headers = ["config", "n"] + COLUMNS + ["IC95 combined (par cellule)"]
    rows = []
    for cfg, axes in mean_by_config.items():
        c = ci.get(cfg)
        ci_txt = f"{c['mean']:.2f} [{c['ci95'][0]:.2f}, {c['ci95'][1]:.2f}]" if c else "–"
        rows.append([cfg, str(reliability.get(cfg, {}).get("n_valid", "?"))]
                    + [_fmt(axes.get(a)) for a in COLUMNS] + [ci_txt])
    out.append(_table(headers, rows))
    out.append("")

    out.append("## Cout par config (cellules valides, moyennes)")
    out.append("")
    if reliability:
        headers = ["config", "tokens", "duree agent (s)", "wall-clock (s)", "tours LLM",
                   "tool calls", "rejets permission", "sous-agents"]
        rows = [[cfg, _fmt_int(rel.get("mean_tokens_total")), _fmt_int(rel.get("mean_agent_s")),
                 _fmt_int(rel.get("mean_wall_clock_s")), _fmt(rel.get("mean_assistant_turns")),
                 _fmt(rel.get("mean_tool_calls")), _fmt(rel.get("mean_permission_rejections")),
                 _fmt(rel.get("mean_subagent_calls"))]
                for cfg, rel in reliability.items()]
        out.append(_table(headers, rows))
        out.append("")

    if delta:
        low, high = compared.get("low", "?"), compared.get("high", "?")
        out.append(f"## Delta {high} − {low} (l'apport du contexte)")
        out.append("")
        out.append(_table(["axe", "delta (moyennes)"],
                          [[a, f"{delta[a]:+.2f}"] for a in COLUMNS if a in delta]))
        out.append("")
        if delta_paired:
            lo_ci, hi_ci = delta_paired["ci95"]
            sig = "significatif" if (round(lo_ci, 2) > 0 or round(hi_ci, 2) < 0) else "NON significatif"
            out.append(f"Delta `combined` apparie par (tache, seed) sur {delta_paired['n']} paires "
                       f"valides : **{delta_paired['mean']:+.2f}** IC95 [{lo_ci:+.2f}, {hi_ci:+.2f}] "
                       f"— {sig} au seuil 5 %.")
            out.append("")
        if n_valid < 20:
            out.append(f"> ⚠️ Echantillon restreint ({n_valid} cellules valides) : ce delta valide que "
                       "le harnais fonctionne, ce n'est pas encore un signal statistiquement "
                       "significatif.")
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
    out.append("- Les axes sans check pour une tache n'entrent pas dans la moyenne combinee de "
               "cette tache ; les checks `skipped` (➖) n'entrent dans aucun axe.")
    out.append("- Une cellule `timeout` est notee sur ce qu'elle a produit avant le budget : "
               "un score eleve avec timeout signifie \"bon resultat, trop lent\".")

    return "\n".join(out) + "\n"
