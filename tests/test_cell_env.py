"""Identite git des cellules (bench/cellenv.py) : fournie en mode pod comme en mode process, pour
les deux agents ; et verifiee avec un VRAI git dans un environnement sans aucune configuration,
comme un pod de cellule."""
import os
import subprocess
from pathlib import Path

import pytest

from bench import k8s
from bench.cellenv import (
    GIT_IDENTITY_EMAIL,
    GIT_IDENTITY_ENV,
    GIT_IDENTITY_META,
    GIT_IDENTITY_NAME,
)
from bench.claude_driver import ClaudeCodeDriver
from bench.opencode_driver import RealOpenCodeDriver
from bench.schema import TaskSpec


def _task():
    return TaskSpec(id="t", prompt="p", dir=Path("."), grade_fn=lambda c: [], timeout_s=5)


def test_identity_constants_are_consistent():
    assert GIT_IDENTITY_ENV == {"GIT_AUTHOR_NAME": "bench", "GIT_AUTHOR_EMAIL": "bench@local",
                                "GIT_COMMITTER_NAME": "bench", "GIT_COMMITTER_EMAIL": "bench@local"}
    assert GIT_IDENTITY_META == "bench <bench@local> (env)"


def test_pod_job_carries_the_identity_in_the_container_env():
    m = k8s.build_job_manifest(name="n", namespace="ns", image="img", secret_name="s", run_id="r",
                               task_id="t", config_id="C0", seed=0, model="m", sleep_seconds=1,
                               active_deadline_s=1, ttl_after_finished_s=1, resources={})
    c = m["spec"]["template"]["spec"]["containers"][0]
    assert {e["name"]: e["value"] for e in c["env"]} == GIT_IDENTITY_ENV
    assert c["envFrom"] == [{"secretRef": {"name": "s"}}]       # le Secret des identifiants reste


@pytest.mark.parametrize("driver_cls", [RealOpenCodeDriver, ClaudeCodeDriver])
def test_process_drivers_pass_the_identity_to_the_agent(driver_cls, tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw["env"])
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "someone-else")        # l'hote ne doit pas primer
    driver_cls().run(_task(), tmp_path, "m", 0, "C0")
    for k, v in GIT_IDENTITY_ENV.items():
        assert seen[k] == v, k


def _clean_git_env(home: Path) -> dict:
    """Environnement d'un pod de cellule : aucune configuration git (ni globale, ni systeme)."""
    return {"PATH": os.environ["PATH"], "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}


def _commit(repo: Path, env: dict):
    (repo / "analyse.md").write_text("x\n")
    subprocess.run(["git", "add", "analyse.md"], cwd=repo, env=env, check=True, capture_output=True)
    return subprocess.run(["git", "commit", "-q", "-m", "analyse"], cwd=repo, env=env,
                          capture_output=True, text=True)


def test_real_git_without_identity_fails_exactly_as_in_the_transcripts(tmp_path):
    """Temoin negatif : sans identite, le commit echoue avec le message vu dans les transcripts."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=_clean_git_env(tmp_path))
    r = _commit(tmp_path, _clean_git_env(tmp_path))
    assert r.returncode != 0
    assert "unable to auto-detect email address" in r.stderr or "Please tell me who you are" in r.stderr


def test_real_git_with_the_cell_identity_commits(tmp_path):
    env = {**_clean_git_env(tmp_path), **GIT_IDENTITY_ENV}
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=env)
    r = _commit(tmp_path, env)
    assert r.returncode == 0, r.stderr
    who = subprocess.run(["git", "log", "-1", "--format=%an <%ae> / %cn <%ce>"], cwd=tmp_path,
                         env=env, capture_output=True, text=True).stdout.strip()
    assert who == f"{GIT_IDENTITY_NAME} <{GIT_IDENTITY_EMAIL}> / {GIT_IDENTITY_NAME} <{GIT_IDENTITY_EMAIL}>"


def test_identity_is_recorded_in_the_run_meta():
    import inspect

    from bench import cli
    assert "git_identity" in inspect.getsource(cli.cmd_run)
