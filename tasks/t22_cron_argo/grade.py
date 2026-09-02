"""T22 : CronWorkflow Argo. Manifeste parse (pas regex sur le texte brut), kind CronWorkflow,
schedule cron mensuel valide, chemin S3 expose en parametre de workflow, identifiants via
Secret k8s (secretKeyRef / envFrom.secretRef), image explicite, aucun secret en dur,
`argo lint` best-effort."""
import re

import yaml

from bench.grading import best_effort_render, code_text, no_hardcoded_secrets
from bench.outcome import Check, walk


def _docs(ws):
    out = []
    for p in list(ws.rglob("*.yaml")) + list(ws.rglob("*.yml")):
        if ".git" in p.parts or ".opencode" in p.parts:
            continue
        try:
            for d in yaml.safe_load_all(p.read_text(encoding="utf-8", errors="replace")):
                if isinstance(d, dict):
                    out.append((p, d))
        except yaml.YAMLError:
            pass
    return out


def grade(ctx):
    ws = ctx.workspace
    docs = _docs(ws)
    cron = [(p, d) for p, d in docs if str(d.get("kind")) == "CronWorkflow"
            and "argoproj.io" in str(d.get("apiVersion"))]
    checks = [Check("cronworkflow_present", bool(cron), 1.0 if cron else 0.0, axis="platform",
                    detail=f"{len(cron)} CronWorkflow parse(s) sur {len(docs)} doc(s) yaml")]
    if not cron:
        return checks + [no_hardcoded_secrets(code_text(ws, ["*.yaml", "*.yml"]))]
    p, d = cron[0]
    spec = d.get("spec", {}) or {}
    sched = str(spec.get("schedule", "") or "")
    fields = sched.split()
    monthly = len(fields) == 5 and fields[2] == "1" and fields[3] == "*"
    checks.append(Check("schedule_monthly_valid", monthly,
                        1.0 if monthly else (0.5 if len(fields) == 5 else 0.0),
                        detail=f"schedule='{sched}'"))
    leaves = list(walk(d))
    joined = " ".join("/".join(path) + "=" + str(v) for path, v in leaves)
    params = [v for path, v in leaves if path and path[-1] == "name"
              and "parameters" in "/".join(path)
              and re.search(r"(?i)s3|path|dest|output|bucket", str(v))]
    checks.append(Check("s3_path_as_parameter", bool(params), 1.0 if params else 0.0,
                        axis="repro", detail=f"parametres: {params[:3]}"))
    secret_ref = "secretKeyRef" in joined or "secretRef" in joined
    checks.append(Check("credentials_via_k8s_secret", secret_ref, 1.0 if secret_ref else 0.0,
                        axis="safety"))
    image = any(path and path[-1] == "image" and v for path, v in leaves)
    checks.append(Check("image_specified", image, 1.0 if image else 0.0, axis="platform"))
    uses_script = "publish_revenu_epci" in joined
    checks.append(Check("invokes_publish_script", uses_script, 1.0 if uses_script else 0.0))
    checks.append(no_hardcoded_secrets(code_text(ws, ["*.yaml", "*.yml"])))
    checks.append(best_effort_render(["argo", "lint", "--offline", str(p.relative_to(ws))], ws,
                                     name="argo_lint"))
    return checks
