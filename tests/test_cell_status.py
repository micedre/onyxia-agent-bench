"""Quelles cellules entrent dans les moyennes.

Deux erreurs symetriques a eviter : compter une defaillance d'infrastructure comme un echec de
l'agent (un cluster sature avait attribue des zeros a toute une tache), et exclure une cellule
ou l'agent a travaille jusqu'au bout parce que le CLI a rendu un code non nul (une cellule C4 a
1.00 partout avait ete ecartee apres huit rejets de permission, ce qui penalisait justement la
config mesuree)."""
from pathlib import Path

import pytest

from bench.schema import VALID_STATUSES, RunResult, Transcript, cell_status


def _run(*, error=None, exit_code=0, timed_out=False, turns=0, tokens_in=0, raw=""):
    t = Transcript(raw_stdout=raw, tokens_in=tokens_in, assistant_turns=turns)
    return RunResult("t", "C0", "m", 0, Path("."), t, exit_code=exit_code,
                     timed_out=timed_out, error=error)


def test_success_is_ok():
    assert cell_status(_run()) == "ok"


def test_agent_worked_then_cli_exited_nonzero_counts():
    """opencode rend 1 apres des rejets de permission automatiques, mais l'agent a produit
    33 tours et 789k tokens et son travail est complet."""
    r = _run(error="exit=1", exit_code=1, turns=33, tokens_in=789_000)
    assert cell_status(r) == "agent_error"
    assert "agent_error" in VALID_STATUSES, "la cellule doit compter dans les moyennes"


def test_nonzero_exit_without_any_activity_never_ran():
    """Cas t11 : le modele refuse l'appel d'outil, exit=1, aucun tour ni token."""
    r = _run(error="exit=1", exit_code=1, turns=0, tokens_in=0)
    assert cell_status(r) == "never_ran"
    assert "never_ran" not in VALID_STATUSES


@pytest.mark.parametrize("kwargs,expected", [
    (dict(error="exit=124", exit_code=124, turns=5, tokens_in=1000), "timeout"),
    (dict(error="exit=1", exit_code=1, turns=5, tokens_in=1000, raw="x\n[TIMEOUT]"), "timeout"),
    (dict(error="exit=137", exit_code=137, turns=5, tokens_in=1000), "oom"),
    (dict(error="pod jamais pret : ...", exit_code=1), "never_ran"),
])
def test_other_statuses_unchanged(kwargs, expected):
    assert cell_status(_run(**kwargs)) == expected


def test_only_agent_statuses_are_valid():
    assert set(VALID_STATUSES) == {"ok", "timeout", "agent_error"}
