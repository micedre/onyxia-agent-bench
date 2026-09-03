"""Notation de T04 : projet Python reproductible, verifie dans l'environnement du projet
(uv run) - pas dans le venv du harnais."""
from __future__ import annotations

import tomllib

from bench.grading import (
    Check,
    file_exists,
    gitignore_blocks,
    pytest_passes,
    python_runs,
    test_files,
)


def _lint_config(ws) -> Check:
    for name in ("ruff.toml", ".ruff.toml", "setup.cfg", ".flake8", "tox.ini"):
        if (ws / name).is_file():
            return Check("lint_config", True, 1.0, detail=name)
    pp = ws / "pyproject.toml"
    if pp.is_file():
        try:
            data = tomllib.loads(pp.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            return Check("lint_config", False, 0.0, detail=f"pyproject.toml invalide: {e}")
        tool = data.get("tool", {})
        for linter in ("ruff", "flake8", "pylint", "black"):
            if linter in tool:
                return Check("lint_config", True, 1.0, detail=f"[tool.{linter}]")
        deps = " ".join(data.get("project", {}).get("dependencies", []))
        groups = data.get("dependency-groups", {})
        deps += " " + " ".join(str(v) for v in groups.values())
        if "ruff" in deps:
            return Check("lint_config", True, 0.5, detail="ruff en dependance mais pas configure")
    return Check("lint_config", False, 0.0, detail="aucune config de lint (ruff/flake8/pylint)")


def grade(ctx):
    ws = ctx.workspace
    checks = []
    checks.append(file_exists(ws, ["uv.lock", "pylock.toml", "requirements.lock", "poetry.lock",
                                   "pixi.lock", "requirements*.txt"],
                              name="lockfile", axis="repro"))
    lock = checks[-1]
    if lock.passed and lock.detail.startswith("requirements"):
        # un requirements.txt ne vaut lockfile que s'il fige les versions
        txt = (ws / lock.detail).read_text(encoding="utf-8", errors="replace")
        pinned = all("==" in l for l in txt.splitlines() if l.strip() and not l.startswith("#"))
        lock.score = 1.0 if pinned else 0.5
        lock.detail += " (versions figees)" if pinned else " (versions non figees)"
    checks.append(file_exists(ws, ["pyproject.toml"], name="pyproject", axis="repro"))
    checks.append(_lint_config(ws))
    checks.append(gitignore_blocks(ws, [("data/x.csv", "data/"), (".env", "secrets.env")]))
    checks.append(Check("tests_present", bool(test_files(ws, ["test_*.py", "*_test.py"])),
                        1.0 if test_files(ws, ["test_*.py", "*_test.py"]) else 0.0,
                        detail="tests trouves" if test_files(ws, ["test_*.py", "*_test.py"])
                        else "aucun test"))
    checks.append(pytest_passes(ws))
    checks.append(python_runs(ws, ["analyse.py", "**/analyse*.py", "main.py", "*.py"],
                              name="analysis_still_runs"))
    return checks
