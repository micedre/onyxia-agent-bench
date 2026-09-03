"""Notation de T05 : scaffold R reproductible. Double comme test de routage (le depot
contient deja un script .R ; un agent bien configure doit rester en R plutot que de retomber
sur des outils Python)."""
from bench.grading import Check, deliverable_files, file_exists, gitignore_blocks, r_tests_pass


def grade(ctx):
    ws = ctx.workspace
    checks = []
    checks.append(file_exists(ws, ["renv.lock"], name="lockfile_r", axis="repro"))
    checks.append(file_exists(ws, ["_targets.R", "DESCRIPTION", "Makefile", "_targets.yaml"],
                              name="pipeline_or_project_r", axis="repro"))
    checks.append(file_exists(ws, ["tests/testthat/test-*.R", "tests/testthat.R", "tests/*.R"],
                              name="tests_r_present", axis="functional", include_tests=True))
    checks.append(r_tests_pass(ws))
    # renv::init() ecrit renv/.gitignore (pas un .gitignore racine) : evalue avec git check-ignore
    checks.append(gitignore_blocks(ws, [(".Rhistory", ".RData", ".Rproj.user/x"),
                                        ("renv/library/x", "renv/staging/x")],
                                   name="gitignore_blocks_renv"))
    # routage : la reponse est en R, pas un projet Python glisse a la place
    r_files = deliverable_files(ws, ["*.R", "*.Rmd", "*.qmd", "DESCRIPTION", "renv.lock"])
    py_files = [p for p in deliverable_files(ws, ["*.py", "pyproject.toml"])]
    stays_r = bool(r_files) and not py_files
    checks.append(Check("stays_in_r", stays_r, 1.0 if stays_r else 0.0, axis="functional",
                        detail=f"fichiers R={len(r_files)}, fichiers Python={len(py_files)}"))
    return checks
