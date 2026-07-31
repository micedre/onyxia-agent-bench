"""Notation offline de T06 : structure d'un manifeste Argo Workflows (pas de soumission
reelle au cluster dans cette v0 - voir les limites connues du rapport)."""
from bench.grading import code_contains, code_text, file_exists, no_hardcoded_secrets


def grade(ctx):
    checks = []
    checks.append(file_exists(ctx.workspace, ["*.yaml", "*.yml"], name="manifest_present",
                              needle="argoproj.io"))
    text = code_text(ctx.workspace, ["*.yaml", "*.yml"])
    checks.append(code_contains(text, [r"kind:\s*(Workflow|CronWorkflow)"],
                                name="kind_workflow_present", axis="platform"))
    checks.append(code_contains(text, [r"templates:", r"steps:", r"dag:"],
                                name="steps_present", axis="functional"))
    checks.append(no_hardcoded_secrets(text))
    return checks
