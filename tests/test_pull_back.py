"""Rapatriement du workspace : exclusions de volume, delai large, et echec = defaut d'infra."""
from bench import k8s
from bench.schema import PULL_FAILED_MARKER, RunResult, Transcript, cell_status


def test_pull_command_excludes_rebuildable_directories():
    cmd = k8s.pull_command("/work")
    for d in (".venv", "node_modules", "__pycache__", ".pytest_cache"):
        assert f"--exclude={d}" in cmd
    assert cmd[:3] == ["tar", "-C", "/work"] and cmd[-3:] == ["-czf", "-", "."]
    assert all(c.startswith("--exclude") for c in cmd[3:-3])   # exclusions avant les arguments d'archive


def test_pull_timeout_is_generous():
    assert k8s.PULL_TIMEOUT_S >= 300


def _run(raw, **kw):
    t = Transcript(raw_stdout=raw, tokens_in=1000, assistant_turns=5)
    return RunResult("t03", "C4", "m", 0, None, t, **kw)


def test_failed_pull_is_an_infrastructure_error_even_with_a_clean_exit():
    assert cell_status(_run(f"ok\n[STDERR]\n{PULL_FAILED_MARKER} timed out", exit_code=0)) == "error"


def test_failed_pull_is_not_an_agent_error():
    # sans le marqueur, une erreur avec des tours et des tokens reste une erreur d'agent
    assert cell_status(_run("x", exit_code=1, error="exit=1")) == "agent_error"
    assert cell_status(_run(PULL_FAILED_MARKER, exit_code=1, error="exit=1")) == "error"


def test_clean_cell_stays_ok():
    assert cell_status(_run("fine", exit_code=0)) == "ok"
