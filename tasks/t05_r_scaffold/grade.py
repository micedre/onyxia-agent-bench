"""Notation offline de T05 : scaffold R reproductible. Double comme test de routage (le
depot contient deja un script .R ; un agent bien configure doit reconnaitre le contexte R
plutot que de retomber par defaut sur des outils Python)."""
from bench.grading import file_exists, gitignore_blocks


def grade(ctx):
    checks = []
    checks.append(file_exists(ctx.workspace, ["renv.lock"], name="lockfile_r", axis="repro"))
    checks.append(file_exists(ctx.workspace, ["_targets.R", "DESCRIPTION"],
                              name="pipeline_or_project_r", axis="repro"))
    checks.append(file_exists(ctx.workspace, ["tests/testthat/test-*.R", "tests/testthat.R"],
                              name="tests_r_present", axis="functional"))
    checks.append(gitignore_blocks(ctx.workspace, [".Rhistory", "renv/library"],
                                   name="gitignore_blocks_renv"))
    return checks
