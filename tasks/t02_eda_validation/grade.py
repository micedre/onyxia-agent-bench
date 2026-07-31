"""Notation offline de T02 : le script produit fait de l'EDA + une validation explicite."""
from bench.grading import Check, code_contains, code_text, file_exists, python_runs


def grade(ctx):
    checks = []
    checks.append(file_exists(ctx.workspace, ["*.py"], name="script_present"))
    checks.append(python_runs(ctx.workspace, ["*.py"]))
    text = code_text(ctx.workspace, ["*.py"])
    checks.append(code_contains(
        text, [r"\.describe\(", r"\.isna\(", r"\.isnull\(", r"value_counts\("],
        name="eda_calls_present"))
    checks.append(code_contains(
        text, [r"assert\b", r"raise\s+ValueError", r"\.dropna\(", r"< ?0", r"clip\("],
        name="validation_step_present"))
    return checks
