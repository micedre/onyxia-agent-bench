"""Notation de T06 : structure d'un manifeste Argo Workflows, evaluee sur le YAML PARSE
(pas de soumission reelle au cluster) : 3 etapes nommees, ordonnees, image, creds via env."""
from __future__ import annotations

import json
import re

from bench.grading import Check, code_text, no_hardcoded_secrets, yaml_documents

KINDS = {"Workflow", "CronWorkflow", "WorkflowTemplate", "ClusterWorkflowTemplate"}
STAGES = {"prepare": r"prep|clean|nettoy", "train": r"train|entra[iî]n|fit|model",
          "mlflow": r"mlflow|log"}


def _templates(spec: dict) -> list[dict]:
    if "workflowSpec" in spec:
        spec = spec["workflowSpec"]
    return [t for t in spec.get("templates", []) if isinstance(t, dict)]


def _walk(obj, key):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            yield from _walk(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, key)


def grade(ctx):
    ws = ctx.workspace
    checks = []
    docs = [(p, d) for p, d in yaml_documents(ws)
            if str(d.get("apiVersion", "")).startswith("argoproj.io")]
    text = code_text(ws, ["*.yaml", "*.yml"])
    checks.append(Check("manifest_present", bool(docs), 1.0 if docs else 0.0,
                        detail=", ".join(str(p.name) for p, _ in docs) or
                        "aucun YAML valide avec apiVersion argoproj.io"))
    if not docs:
        checks += [Check("kind_workflow_present", False, 0.0, axis="platform", detail="-"),
                   Check("three_stages", False, 0.0, weight=2.0, detail="-"),
                   Check("stages_ordered", False, 0.0, detail="-"),
                   Check("creds_from_env", False, 0.0, axis="platform", detail="-"),
                   Check("image_specified", False, 0.0, weight=0.5, detail="-")]
        checks.append(no_hardcoded_secrets(text))
        return checks

    kinds = {str(d.get("kind")) for _, d in docs}
    ok_kind = bool(kinds & KINDS)
    checks.append(Check("kind_workflow_present", ok_kind, 1.0 if ok_kind else 0.0,
                        axis="platform", detail=f"kinds={sorted(kinds)}"))

    # etapes : noms de templates + commandes/args des containers/scripts
    templates = []
    for _, d in docs:
        templates += _templates(d.get("spec", {}))
    blob = json.dumps(templates, ensure_ascii=False).lower()
    found = {k: bool(re.search(v, blob)) for k, v in STAGES.items()}
    exec_templates = [t for t in templates if any(k in t for k in ("container", "script"))]
    s = sum(found.values()) / 3 if len(exec_templates) >= 3 or len(templates) >= 4 else \
        min(sum(found.values()) / 3, 0.5)
    checks.append(Check("three_stages", s >= 0.99, s, weight=2.0,
                        detail=f"etapes reconnues={found}, templates executables={len(exec_templates)}"))

    # ordre : dag avec dependencies/depends, ou steps sequentiels (>= 3 groupes)
    dag_deps = [t for t in _walk(templates, "tasks") if isinstance(t, list)
                and sum(1 for x in t if isinstance(x, dict) and (x.get("dependencies") or x.get("depends"))) >= 2]
    step_groups = [g for g in _walk(templates, "steps") if isinstance(g, list) and len(g) >= 3]
    ordered = bool(dag_deps or step_groups)
    checks.append(Check("stages_ordered", ordered, 1.0 if ordered else 0.0,
                        detail="dag avec dependances" if dag_deps else
                        ("steps sequentiels" if step_groups else "pas d'ordre explicite (dag/steps)")))

    # plateforme : creds via env (secretKeyRef/envFrom/valueFrom), pas de valeurs en dur
    env_ok = bool(re.search(r"secretKeyRef|envFrom|valueFrom|serviceAccountName", text))
    checks.append(Check("creds_from_env", env_ok, 1.0 if env_ok else 0.0, axis="platform",
                        detail="ok" if env_ok else "aucun secretKeyRef/envFrom/valueFrom/serviceAccountName"))
    images = [c.get("image") for c in _walk(templates, "container") if isinstance(c, dict)]
    images += [c.get("image") for c in _walk(templates, "script") if isinstance(c, dict)]
    img_ok = bool(images) and all(images)
    checks.append(Check("image_specified", img_ok, 1.0 if img_ok else 0.0, weight=0.5,
                        detail=f"images={sorted({str(i) for i in images})[:3]}"))
    checks.append(no_hardcoded_secrets(text))
    return checks
