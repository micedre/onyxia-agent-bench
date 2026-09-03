"""Choix du jeu de taches (`--suite`).

Le defaut est la suite `context` : sur le run qwen3-6-all (17 taches x C0/C4 x 5 seeds), le
delta appari e valait +0.27 IC95 [+0.06, +0.48] sur ces taches et -0.04 sur les autres, soit
+0.04 non significatif sur l'ensemble. Lancer les 17 par defaut noyait le signal mesure."""
from pathlib import Path

import pytest

from bench.cli import select_tasks
from bench.registry import discover_tasks

REPO = Path(__file__).resolve().parent.parent
TASKS = discover_tasks(REPO / "tasks")
CONTEXT = {"t03_mlflow_train", "t09_secret_trap", "t10_diag_403",
           "t18_notebook_refactor", "t23_code_review"}


def _ids(sel):
    return {t.id for t in sel}


def test_context_suite_is_exactly_the_five():
    assert _ids(select_tasks(TASKS, None, "context")) == CONTEXT


def test_model_suite_is_the_rest_and_they_partition():
    model = _ids(select_tasks(TASKS, None, "model"))
    assert model == set(TASKS) - CONTEXT
    assert model & CONTEXT == set()
    assert model | CONTEXT == set(TASKS)


def test_all_returns_every_task():
    assert _ids(select_tasks(TASKS, None, "all")) == set(TASKS)


def test_explicit_tasks_win_over_suite():
    """`--tasks` sert a l'ad hoc : il doit primer, meme sur une tache d'une autre suite."""
    sel = select_tasks(TASKS, "t14_fix_bug_script,t17_dvf_dedup", "context")
    assert _ids(sel) == {"t14_fix_bug_script", "t17_dvf_dedup"}
    assert _ids(select_tasks(TASKS, "all", "context")) == set(TASKS)


def test_unknown_suite_is_refused_not_silently_empty():
    with pytest.raises(SystemExit):
        select_tasks(TASKS, None, "inexistante")


def test_every_task_declares_a_known_suite():
    assert {t.suite for t in TASKS.values()} <= {"context", "model"}


def test_counter_case_is_present_in_the_default_suite():
    """t18 est la seule tache du jeu par defaut ou C4 perd (-0.33). La retirer rendrait la
    suite incapable de montrer que le contexte nuit, donc son resultat acquis d'avance."""
    assert "t18_notebook_refactor" in CONTEXT
    assert TASKS["t18_notebook_refactor"].suite == "context"
