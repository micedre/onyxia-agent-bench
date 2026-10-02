"""--isolation pod avec --agent claude : Secret, commande exec, installation, cycle d'une cellule.
Aucun cluster ni jeton : `bench.k8s` et `subprocess.run` sont remplaces."""
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from bench import cli, k8s
from bench.claude_driver import DEFAULT_POD_CLAUDE_VERSION, POD_CLAUDE_INSTALLER, PodClaudeDriver
from bench.opencode_driver import PodOpenCodeDriver
from bench.schema import TaskSpec

TOKEN = "fake-oauth-token-for-tests-only"
RES = {"requests": {"cpu": "1"}, "limits": {"cpu": "1"}}


def _task(timeout_s=60):
    return TaskSpec(id="t", prompt="fais 'X'", dir=Path("."), grade_fn=lambda c: [],
                    timeout_s=timeout_s)


def _driver(cls=PodClaudeDriver, **kw):
    return cls(image="img:1", namespace="ns", secret_name="s", run_id="r", resources=RES, **kw)


# ------------------------------------------------------------------ Secret


def test_secret_is_generic():
    m = k8s.build_secret_manifest(name="n", namespace="ns", run_id="r",
                                  string_data={"A": "1", "B": "2"})
    assert m["kind"] == "Secret" and m["stringData"] == {"A": "1", "B": "2"}


def test_secret_data_per_agent(monkeypatch):
    for k in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "OPENCODE_ONYXIA_BASE_URL",
              "OPENCODE_ONYXIA_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SystemExit, match="claude setup-token"):
        cli._pod_secret_data("claude")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", TOKEN)
    assert cli._pod_secret_data("claude") == {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert set(cli._pod_secret_data("claude")) == {"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"}
    # opencode : inchange, et ne recoit jamais le jeton Claude
    with pytest.raises(SystemExit, match="OPENCODE_ONYXIA"):
        cli._pod_secret_data("opencode")
    monkeypatch.setenv("OPENCODE_ONYXIA_BASE_URL", "u")
    monkeypatch.setenv("OPENCODE_ONYXIA_API_KEY", "k")
    assert cli._pod_secret_data("opencode") == {"OPENCODE_ONYXIA_BASE_URL": "u",
                                                "OPENCODE_ONYXIA_API_KEY": "k"}


# ------------------------------------------------------------------ commande


def test_exec_command_is_headless_isolated_and_token_free(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", TOKEN)
    cmd = _driver().exec_command(_task(90), "claude-opus-5-5")
    assert cmd.startswith("cd /tmp/bench-cell && ")
    assert "timeout 90s claude -p " in cmd and "--setting-sources project" in cmd
    assert "--model claude-opus-5-5" in cmd and cmd.endswith("< /dev/null")
    assert "CLAUDE_CONFIG_DIR=/tmp/bench-claude-config" in cmd   # hors du workdir pousse/rapatrie
    assert "PATH=$HOME/.local/bin:$PATH" in cmd                    # installeur officiel -> ~/.local/bin
    assert "DISABLE_AUTOUPDATER=1" in cmd                          # la version epinglee ne derive pas
    import shlex
    argv = shlex.split(cmd.split("&&", 1)[1])                      # le shell du pod relit ceci
    assert argv[argv.index("-p") + 1] == "fais 'X'"                # prompt intact apres echappement
    assert TOKEN not in cmd and "OAUTH" not in cmd


def test_opencode_pod_command_unchanged():
    cmd = _driver(PodOpenCodeDriver).exec_command(_task(90), "onyxia/m")
    assert "opencode run --agent build" in cmd and "XDG_CONFIG_HOME=/tmp/bench-xdg-config" in cmd
    assert "claude" not in cmd


def test_default_install_uses_the_official_installer_pinned_and_no_node():
    """Les images Onyxia n'ont ni node ni npm : l'installation par defaut ne doit dependre que
    de curl et bash (verifie sur la chaine d'images InseeFrLab/images-datascience)."""
    d = _driver()
    assert d.install_cmd == (f"curl -fsSL {POD_CLAUDE_INSTALLER} | "
                             f"bash -s {DEFAULT_POD_CLAUDE_VERSION}")
    assert "npm" not in d.install_cmd and "node" not in d.install_cmd
    assert _driver(claude_version="stable").install_cmd.endswith("bash -s stable")
    assert _driver(install_cmd="curl x | sh").install_cmd == "curl x | sh"


def test_pod_images_are_pinned_tags():
    import re
    dated = r"r\d+\.\d+\.\d+-py\d+\.\d+\.\d+-\d{4}\.\d{2}\.\d{2}"
    assert re.fullmatch(rf"inseefrlab/onyxia-vscode-r-python-julia:{dated}",
                        k8s.UPSTREAM_POD_IMAGE), k8s.UPSTREAM_POD_IMAGE
    assert re.fullmatch(rf"ghcr\.io/micedre/onyxia-agent-bench-pod:{dated}-claude\d+\.\d+\.\d+",
                        k8s.DEFAULT_POD_IMAGE), k8s.DEFAULT_POD_IMAGE


def test_pod_image_defaults_when_not_given(monkeypatch):
    seen = {}

    def stop(args, tasks, run_id):
        seen["image"] = args.pod_image
        raise RuntimeError("stop")
    monkeypatch.setattr(cli, "_build_pod_driver", stop)
    # --no-preflight : ce test porte sur l'image par defaut, pas sur le pre-controle de l'agent
    argv = ["run", "--tasks", "t10_diag_403", "--isolation", "pod", "--agent", "claude",
            "--model", "m", "--no-preflight"]
    with pytest.raises(RuntimeError, match="stop"):
        cli.main(argv)
    assert seen["image"] == k8s.DEFAULT_POD_IMAGE
    with pytest.raises(RuntimeError, match="stop"):
        cli.main(argv + ["--pod-image", "mine:1"])
    assert seen["image"] == "mine:1"


# ------------------------------------------------------------------ cycle d'une cellule


STREAM = "\n".join([
    '{"type":"assistant","message":{"usage":{"input_tokens":10,"output_tokens":5},'
    '"content":[{"type":"text","text":"Le jeton a expire."}]}}',
    '{"type":"result","subtype":"success","total_cost_usd":0.01,"num_turns":1}'])


class FakeKube:
    """Remplace subprocess.run (kubectl exec) et les helpers k8s utilises par _exec_cell."""

    def __init__(self, monkeypatch, *, has_claude=False, install_rc=0, post_install_has=True):
        self.calls, self.installed = [], False
        self.has_claude, self.install_rc, self.post = has_claude, install_rc, post_install_has
        monkeypatch.setattr(k8s, "check_binary", lambda pod, ns, b, **k: True)
        monkeypatch.setattr(k8s, "push_workspace", lambda *a, **k: None)
        monkeypatch.setattr(k8s, "pull_workspace", lambda *a, **k: None)
        monkeypatch.setattr(subprocess, "run", self.run)

    def run(self, args, **kw):
        sh = args[-1]
        self.calls.append(sh)
        if "command -v claude" in sh:
            ok = self.has_claude or (self.installed and self.post)
            return subprocess.CompletedProcess(args, 0 if ok else 1, "", "")
        if "install.sh" in sh or sh == "my-install":
            self.installed = self.install_rc == 0
            return subprocess.CompletedProcess(args, self.install_rc, "", "curl: (6) Could not resolve host")
        return subprocess.CompletedProcess(args, 0, STREAM, "")


def _cell(driver, tmp_path):
    return driver._exec_cell(_task(), tmp_path, "claude-opus-5-5", 0, "C4", "pod-1", 0.0)


def test_cell_installs_claude_then_runs_and_parses(monkeypatch, tmp_path):
    kube = FakeKube(monkeypatch)
    res = _cell(_driver(), tmp_path)
    assert res.error is None and res.exit_code == 0
    assert res.transcript.assistant_turns == 1 and res.transcript.cost == 0.01
    assert res.transcript.text == "Le jeton a expire."
    cmds = kube.calls
    assert any("install.sh" in c for c in cmds)
    # l'installation precede l'execution de l'agent, et l'agent ne la voit pas dans agent_s
    assert next(i for i, c in enumerate(cmds) if "install.sh" in c) < \
        next(i for i, c in enumerate(cmds) if "claude -p" in c)
    assert res.agent_s <= res.wall_clock_s


def test_cell_skips_install_when_claude_present(monkeypatch, tmp_path):
    kube = FakeKube(monkeypatch, has_claude=True)
    res = _cell(_driver(), tmp_path)
    assert res.error is None and not any("install.sh" in c for c in kube.calls)


def test_install_failure_is_never_ran_not_an_agent_zero(monkeypatch, tmp_path):
    from bench.schema import cell_status
    FakeKube(monkeypatch, install_rc=1)
    res = _cell(_driver(), tmp_path)
    assert res.exit_code == 127 and "installation de claude echouee" in res.error
    assert "Could not resolve host" in res.error and "curl/bash" in res.error
    assert cell_status(res) == "never_ran"          # exclue des moyennes


def test_install_ok_but_binary_missing(monkeypatch, tmp_path):
    FakeKube(monkeypatch, post_install_has=False)
    res = _cell(_driver(), tmp_path)
    assert "introuvable apres l'installation" in res.error


def test_custom_install_cmd_is_used(monkeypatch, tmp_path):
    kube = FakeKube(monkeypatch)
    res = _cell(_driver(install_cmd="my-install"), tmp_path)
    assert res.error is None and "my-install" in kube.calls


def test_opencode_pod_still_checks_its_binary(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(k8s, "check_binary", lambda pod, ns, b, **k: seen.append(b) or b == "tar")
    res = _driver(PodOpenCodeDriver)._exec_cell(_task(), tmp_path, "m", 0, "C0", "p", 0.0)
    assert seen == ["tar", "opencode"] and res.exit_code == 127 and "'opencode' absent" in res.error


# ------------------------------------------------------------------ CLI


def test_cli_builds_claude_pod_driver_and_secret(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", TOKEN)
    applied, deleted = [], []
    monkeypatch.setattr(k8s, "sweep_orphans", lambda *a, **k: None)
    monkeypatch.setattr(k8s, "kubectl_apply", lambda m, **k: applied.append(m))
    monkeypatch.setattr(k8s, "delete", lambda *a, **k: deleted.append(a))
    args = SimpleNamespace(
        agent="claude", pod_image="img:1", pod_namespace="ns", pod_orphan_max_age_s=None,
        pod_cpu_request="1", pod_mem_request="1Gi", pod_cpu_limit="1", pod_mem_limit="1Gi",
        pod_ready_timeout_s=10, pod_ready_retries=0, pod_claude_version="9.9.9",
        pod_claude_install_cmd=None)
    driver, cleanup = cli._build_pod_driver(args, [_task()], "run1")
    assert isinstance(driver, PodClaudeDriver) and driver.install_cmd.endswith("bash -s 9.9.9")
    assert applied[0]["stringData"] == {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN}
    cleanup()
    assert deleted and deleted[0][0] == "secret"


def test_cli_no_longer_refuses_pod_for_claude():
    import inspect
    assert "pas encore supporte" not in inspect.getsource(cli.cmd_run)


def test_process_driver_keeps_oauth_token(monkeypatch, tmp_path):
    """Regression : le menage des variables CLAUDE_CODE_* ne doit pas retirer le jeton."""
    from bench.claude_driver import ClaudeCodeDriver
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", TOKEN)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent")
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw["env"])
        return subprocess.CompletedProcess(cmd, 0, STREAM, "")
    monkeypatch.setattr(subprocess, "run", fake_run)
    ClaudeCodeDriver().run(_task(), tmp_path, "m", 0, "C0")
    assert seen.get("CLAUDE_CODE_OAUTH_TOKEN") == TOKEN and "CLAUDE_CODE_SESSION_ID" not in seen
