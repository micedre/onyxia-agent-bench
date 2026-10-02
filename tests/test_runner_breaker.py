"""Disjoncteur du run, message de l'agent dans les enregistrements et le rapport, et cablage du
pre-controle dans la CLI. Pilote factice : aucun modele, aucun cluster."""
import itertools
import threading
import time
from pathlib import Path

import pytest

from bench import cli, k8s
from bench.configs import load_ladder
from bench.mlflow_logging import NullLogger
from bench.opencode_driver import BaseDriver
from bench.registry import discover_tasks
from bench.report import frequent_messages, render_markdown
from bench.runner import agent_message, is_systematic_failure, run_benchmark
from bench.schema import Event, RunResult, Transcript

REPO = Path(__file__).resolve().parent.parent
TASK = discover_tasks(REPO / "tasks")["t10_diag_403"]
BASE, CONFIGS = load_ladder(REPO / "configs")
MSG = "Failed to authenticate. API Error: 401 OAuth access token is invalid."


class FakeDriver(BaseDriver):
    """Chaque cellule echoue (un tour, zero token, comme le run reel) ou reussit, selon `script(n)`."""
    name = "fake"

    def __init__(self, script, delay: float = 0.0):
        self.script, self.delay = script, delay
        self.counter, self.started = itertools.count(), 0
        self._lock = threading.Lock()

    def run(self, task, workspace, model, seed, config_id):
        with self._lock:
            n = next(self.counter)
            self.started += 1
        time.sleep(self.delay)
        if self.script(n) == "fail":
            # comme une vraie cellule en echec : le message de l'agent est un evenement (d'ou le
            # statut `error`, et non `never_ran` qui suppose un transcript vide)
            t = Transcript(text=MSG, assistant_turns=1, raw_stdout="{}",
                           events=[Event("message", text=MSG, turn=1)])
            return RunResult(task.id, config_id, model, seed, workspace, t, exit_code=1,
                             wall_clock_s=5.0, error="exit=1")
        t = Transcript(text="Le jeton S3 a expire apres 7 jours.", tokens_in=100, tokens_out=10,
                       assistant_turns=3, raw_stdout="{}")
        return RunResult(task.id, config_id, model, seed, workspace, t, wall_clock_s=5.0)


def _run(tmp_path, script, *, seeds=10, limit=3, workers=1, delay=0.0):
    driver = FakeDriver(script, delay)
    summary = run_benchmark([TASK], [CONFIGS["C0"], CONFIGS["C4"]], BASE, REPO / "configs",
                            "m", seeds, driver, tmp_path / "run", NullLogger(), workers=workers,
                            max_consecutive_failures=limit)
    return summary, driver


# ------------------------------------------------------------------ disjoncteur


def test_systematic_failure_stops_the_run_early(tmp_path):
    summary, driver = _run(tmp_path, lambda n: "fail", seeds=10, limit=3)          # 20 cellules
    ab = summary["meta"]["aborted"]
    assert len(summary["records"]) == 3 and driver.started == 3
    assert ab["cells_skipped"] == 17
    assert "3 cellules perdues d'affilee" in ab["reason"] and MSG in ab["last_message"]
    assert summary["n_cells"] == 3 and summary["n_valid"] == 0


def test_a_valid_cell_resets_the_streak(tmp_path):
    summary, driver = _run(tmp_path, lambda n: "ok" if n % 3 == 2 else "fail", limit=3)
    assert "aborted" not in summary["meta"] and len(summary["records"]) == 20
    assert summary["n_valid"] == 6                       # 1 cellule sur 3 est valide


def test_limit_zero_disables_the_breaker(tmp_path):
    summary, driver = _run(tmp_path, lambda n: "fail", limit=0)
    assert "aborted" not in summary["meta"] and len(summary["records"]) == 20


def test_running_cells_finish_and_nothing_is_lost(tmp_path):
    """Avec des workers, les cellules deja lancees vont au bout (jamais tuees) : lancees + non
    lancees = total, et chaque cellule lancee est enregistree."""
    summary, driver = _run(tmp_path, lambda n: "fail", seeds=10, limit=4, workers=4, delay=0.05)
    ab = summary["meta"]["aborted"]
    assert len(summary["records"]) == driver.started >= 4
    assert driver.started + ab["cells_skipped"] == 20


def test_aborted_run_report_and_summary(tmp_path):
    summary, _ = _run(tmp_path, lambda n: "fail", limit=3)
    md = (tmp_path / "run" / "report.md").read_text(encoding="utf-8")
    assert "RUN INTERROMPU" in md and "3 cellules perdues d'affilee" in md and "17 cellule(s)" in md
    assert MSG in md
    assert "Invocation" in md and "aborted" not in md.split("Invocation")[1].split("\n")[0]


@pytest.mark.parametrize("rec, expected", [
    ({"status": "error", "tokens_total": 0}, True),
    ({"status": "never_ran", "tokens_total": 0}, True),
    ({"status": "error"}, True),
    ({"status": "ok", "tokens_total": 0}, False),
    ({"status": "timeout", "tokens_total": 0}, False),
    ({"status": "agent_error", "tokens_total": 5}, False),
    ({"status": "error", "tokens_total": 5}, False),        # il a travaille : pas systematique
])
def test_is_systematic_failure(rec, expected):
    assert is_systematic_failure(rec) is expected


# ------------------------------------------------------------------ message de l'agent


def test_agent_message_recorded_only_for_lost_or_tokenless_cells(tmp_path):
    summary, _ = _run(tmp_path, lambda n: "ok" if n % 2 else "fail", limit=0, seeds=4)
    for r in summary["records"]:
        if r["status"] == "error":
            assert r["agent_message"] == MSG
        else:
            assert "agent_message" not in r


def test_agent_message_falls_back_to_stderr_tail_and_is_bounded():
    t = Transcript(text="", raw_stdout="{}\n[STDERR]\nligne 1\n" + "x" * 1000 + "\nERREUR FINALE")
    run = RunResult("t", "C0", "m", 0, Path("."), t, exit_code=1, error="exit=1")
    msg = agent_message(run)
    assert msg.endswith("ERREUR FINALE") and len(msg) <= 300       # la FIN de stderr, bornee
    t2 = Transcript(text="  plusieurs\n lignes   espaces ")
    assert agent_message(RunResult("t", "C0", "m", 0, Path("."), t2)) == "plusieurs lignes espaces"
    assert agent_message(RunResult("t", "C0", "m", 0, Path("."), Transcript())) == ""


def test_report_groups_the_most_frequent_messages(tmp_path):
    summary, _ = _run(tmp_path, lambda n: "fail", limit=0, seeds=3)             # 6 cellules
    assert frequent_messages(summary["records"]) == [(MSG, 6)]
    md = render_markdown(summary, summary["meta"])
    assert "Messages d'erreur les plus frequents" in md
    assert f"| 6 | {MSG} |" in md
    assert f"Message de l'agent : « {MSG} »" in md                # et dans chaque cellule


def test_cell_line_shows_the_message(tmp_path, capsys):
    _run(tmp_path, lambda n: "fail", seeds=1, limit=0)
    out = capsys.readouterr().out
    assert f'msg="{MSG[:120]}"' in out


def test_pipe_in_a_message_does_not_break_the_table(tmp_path):
    recs = [{"agent_message": "a | b", "status": "error", "task": "t", "config": "C0", "seed": 0,
             "axis_scores": {}, "checks": []}]
    summary = {"records": recs, "reliability": {"C0": {"n_cells": 1, "n_valid": 0,
                                                       "status_counts": {"error": 1}}},
               "n_cells": 1, "n_valid": 0, "mean_by_config": {}, "compared": {}}
    assert "a \\| b" in render_markdown(summary, {"run_name": "r"})


# ------------------------------------------------------------------ CLI : pre-controle et sortie


def _argv(tmp_path, *extra):
    return ["run", "--tasks", "t10_diag_403", "--configs", "C0", "--seeds", "1", "--no-mlflow",
            "--out", str(tmp_path / "out"), *extra]


def test_failed_preflight_stops_before_any_cluster_object(tmp_path, monkeypatch):
    import bench.preflight as pf
    monkeypatch.setattr(pf, "run_preflight",
                        lambda agent, model: pf.PreflightResult(False, "modele refuse"))
    monkeypatch.setattr(cli, "_build_pod_driver",
                        lambda *a, **k: pytest.fail("un Secret/Job aurait ete cree"))
    with pytest.raises(SystemExit, match="pre-controle echoue pour --agent claude --model opus-5.5"):
        cli.main(_argv(tmp_path, "--agent", "claude", "--model", "opus-5.5", "--isolation", "pod"))
    assert not (tmp_path / "out").exists()


def test_preflight_runs_for_real_runs_not_for_dry_runs_and_can_be_skipped(tmp_path, monkeypatch, capsys):
    import bench.preflight as pf
    calls = []
    monkeypatch.setattr(pf, "run_preflight",
                        lambda agent, model: calls.append((agent, model)) or pf.PreflightResult(True, "ok"))
    monkeypatch.setattr(cli, "make_driver", lambda agent: FakeDriver(lambda n: "ok"))
    cli.main(_argv(tmp_path / "a", "--agent", "opencode"))
    assert calls == [("opencode", "onyxia/qwen3-6-35b-moe")]           # modele par defaut controle
    assert "[preflight] OK : ok" in capsys.readouterr().out
    calls.clear()
    cli.main(_argv(tmp_path / "b", "--agent", "opencode", "--no-preflight"))
    cli.main(_argv(tmp_path / "c", "--agent", "opencode", "--dry-run"))
    assert calls == []


def test_aborted_run_exits_non_zero_with_the_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "make_driver", lambda agent: FakeDriver(lambda n: "fail"))
    with pytest.raises(SystemExit) as e:
        cli.main(["run", "--tasks", "t10_diag_403", "--configs", "C0,C4", "--seeds", "5",
                  "--no-mlflow", "--no-preflight", "--max-consecutive-failures", "2",
                  "--workers", "1", "--out", str(tmp_path / "out")])
    msg = str(e.value)
    assert "run interrompu" in msg and "2 cellules perdues d'affilee" in msg and MSG in msg


def test_preflight_subcommand_exit_codes(monkeypatch, capsys):
    import bench.preflight as pf
    monkeypatch.setattr(pf, "run_preflight", lambda a, m: pf.PreflightResult(True, "bien"))
    with pytest.raises(SystemExit) as e:
        cli.main(["preflight", "--agent", "claude", "--model", "claude-opus-5-5"])
    assert e.value.code == 0 and "[preflight] OK : bien" in capsys.readouterr().out
    monkeypatch.setattr(pf, "run_preflight", lambda a, m: pf.PreflightResult(False, "mal"))
    with pytest.raises(SystemExit) as e:
        cli.main(["preflight", "--agent", "claude", "--model", "x"])
    assert e.value.code == 1
    with pytest.raises(SystemExit, match="--model est requis"):
        cli.main(["preflight", "--agent", "claude"])


def test_default_threshold_is_above_the_default_workers():
    from bench.runner import DEFAULT_MAX_CONSECUTIVE_FAILURES
    assert DEFAULT_MAX_CONSECUTIVE_FAILURES > 4         # 4 = --workers par defaut
    assert k8s.DEFAULT_POD_IMAGE                         # garde-fou d'import
