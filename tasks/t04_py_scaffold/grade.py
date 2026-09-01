"""Notation offline de T04 : verifiable localement (pas besoin de la plateforme)."""
from bench.grading import (
    Check, file_exists, gitignore_blocks, pytest_passes,
)

def grade(ctx):
    checks = []
    # lockfile (uv.lock) OU pyproject avec versions -> reproductibilite
    checks.append(file_exists(ctx.workspace, ["uv.lock", "requirements.lock"],
                              name="lockfile", axis="repro"))
    checks.append(file_exists(ctx.workspace, ["pyproject.toml"], name="pyproject",
                              axis="repro"))
    checks.append(file_exists(ctx.workspace, ["ruff.toml", ".ruff.toml", "pyproject.toml"],
                              name="lint_config", axis="functional",
                              needle="ruff"))  # pyproject compte s'il mentionne ruff
    checks.append(gitignore_blocks(ctx.workspace, ["data", (".env", "*.env", ".env.*")]))
    checks.append(pytest_passes(ctx.workspace))   # tourne les tests s'il y en a
    return checks
