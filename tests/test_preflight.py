"""Pre-controle de l'agent (bench/preflight.py) : vrais executables simules et vrai serveur HTTP
local, jamais de modele ni de reseau externe."""
import http.server
import json
import stat
import threading
from pathlib import Path

import pytest

from bench import preflight
from bench.preflight import check_claude, check_opencode, claude_model_hint, run_preflight

RES = Path(__file__).parent / "resources"


def _fake_claude(tmp_path, stdout: str, rc: int = 0, stderr: str = "") -> str:
    """Un faux binaire `claude` qui rend un flux stream-json donne, et trace ses arguments."""
    (tmp_path / "out.ndjson").write_text(stdout)
    (tmp_path / "err.txt").write_text(stderr)
    exe = tmp_path / "claude"
    exe.write_text(f'#!/bin/bash\nprintf \'%s\\n\' "$@" > "{tmp_path}/args.txt"\n'
                   f'env | grep -E "^CLAUDE_CONFIG_DIR=" > "{tmp_path}/env.txt"\n'
                   f'cat "{tmp_path}/out.ndjson"\ncat "{tmp_path}/err.txt" >&2\nexit {rc}\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


def _ok_stream() -> str:
    return "\n".join(json.dumps(o) for o in (
        {"type": "assistant", "message": {"usage": {"input_tokens": 5, "output_tokens": 1},
                                          "content": [{"type": "text", "text": "OK"}]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": "OK"}))


# ------------------------------------------------------------------ claude


def test_claude_ok(tmp_path):
    r = check_claude("claude-opus-5-5", binary=_fake_claude(tmp_path, _ok_stream()))
    assert r.ok and not r.skipped and "claude-opus-5-5" in r.message
    args = (tmp_path / "args.txt").read_text().splitlines()
    # memes options qu'une cellule : headless, flux json, modele demande, config isolee, pas de MCP
    assert args[:2] == ["-p", "Reponds uniquement par le mot OK."]
    assert "--output-format" in args and "stream-json" in args
    assert args[args.index("--model") + 1] == "claude-opus-5-5"
    assert "--strict-mcp-config" in args and "--setting-sources" in args
    assert (tmp_path / "env.txt").read_text().startswith("CLAUDE_CONFIG_DIR=/")


def test_claude_error_message_is_reported_verbatim_from_the_real_sample(tmp_path):
    """Vraie sortie de `claude -p` v2.1.286 avec un jeton invalide (tests/resources)."""
    real = (RES / "claude_stream_auth_error.ndjson").read_text()
    r = check_claude("claude-opus-5-5", binary=_fake_claude(tmp_path, real, rc=1))
    assert not r.ok
    assert "Failed to authenticate" in r.message and "401" in r.message
    assert "claude-opus-5-5" in r.message
    assert "ne ressemble pas" not in r.message             # identifiant valide : pas d'indice


def test_claude_wrong_model_id_gets_a_hint(tmp_path):
    real = (RES / "claude_stream_auth_error.ndjson").read_text()
    r = check_claude("opus-5.5", binary=_fake_claude(tmp_path, real, rc=1))
    assert not r.ok
    assert "`opus-5.5` ne ressemble pas a un identifiant de modele Claude" in r.message
    assert "claude-opus-5-5" in r.message


def test_claude_without_result_uses_stderr(tmp_path):
    r = check_claude("claude-opus-5-5", binary=_fake_claude(tmp_path, "", rc=3,
                                                            stderr="boom: segfault\n"))
    assert not r.ok and "boom: segfault" in r.message
    r2 = check_claude("claude-opus-5-5", binary=_fake_claude(tmp_path, "", rc=3))
    assert not r2.ok and "code de sortie 3" in r2.message


def test_claude_missing_binary_is_skipped_not_failed():
    r = check_claude("claude-opus-5-5", binary="claude-binaire-introuvable")
    assert r.ok and r.skipped and "ignore" in r.message


def test_claude_timeout(tmp_path, monkeypatch):
    exe = tmp_path / "claude"
    exe.write_text("#!/bin/bash\nsleep 30\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    r = check_claude("claude-opus-5-5", binary=str(exe), timeout=1)
    assert not r.ok and "n'a pas repondu" in r.message


@pytest.mark.parametrize("model, hinted", [
    ("claude-opus-5-5", False), ("claude-haiku-4-5-20251001", False), ("opus", False),
    ("sonnet", False), ("haiku", False), ("opus[1m]", False),
    ("opus-5.5", True), ("opus 5.5", True), ("gpt-4", True), ("onyxia/qwen3", True), ("", True),
])
def test_model_hint(model, hinted):
    assert bool(claude_model_hint(model)) is hinted


# ------------------------------------------------------------------ opencode


class _Models(http.server.BaseHTTPRequestHandler):
    status = 200
    body = {"data": [{"id": "qwen3-8-27b"}, {"id": "gemma4-26b-moe"}]}
    seen_auth = []

    def do_GET(self):  # noqa: N802
        type(self).seen_auth.append(self.headers.get("Authorization"))
        self.send_response(type(self).status)
        self.end_headers()
        if type(self).status == 200:
            self.wfile.write(json.dumps(type(self).body).encode())
        elif type(self).status == 999:
            pass

    def log_message(self, *a):
        pass


@pytest.fixture
def models_server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Models)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Models.status, _Models.seen_auth = 200, []
    _Models.body = {"data": [{"id": "qwen3-8-27b"}, {"id": "gemma4-26b-moe"}]}
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def test_opencode_ok_sends_the_key(models_server):
    r = check_opencode("onyxia/qwen3-8-27b", base_url=models_server, api_key="sekret")
    assert r.ok and not r.skipped and "qwen3-8-27b" in r.message
    assert _Models.seen_auth == ["Bearer sekret"]


def test_opencode_unknown_model_lists_available(models_server):
    r = check_opencode("onyxia/qwen3-6-35b-moe", base_url=models_server, api_key="k")
    assert not r.ok and "qwen3-6-35b-moe" in r.message
    assert "gemma4-26b-moe" in r.message and "qwen3-8-27b" in r.message


@pytest.mark.parametrize("status, needle", [(401, "cle refusee"), (403, "cle refusee"),
                                            (500, "HTTP 500")])
def test_opencode_http_errors(models_server, status, needle):
    _Models.status = status
    r = check_opencode("onyxia/qwen3-8-27b", base_url=models_server, api_key="k")
    assert not r.ok and needle in r.message and f"HTTP {status}" in r.message


def test_opencode_endpoint_without_models_is_skipped(models_server):
    _Models.status = 404
    r = check_opencode("onyxia/qwen3-8-27b", base_url=models_server, api_key="k")
    assert r.ok and r.skipped and "404" in r.message


def test_opencode_unreachable_and_missing_settings(monkeypatch):
    r = check_opencode("onyxia/m", base_url="http://127.0.0.1:9/v1", api_key="k")
    assert not r.ok and "injoignable" in r.message
    monkeypatch.delenv("OPENCODE_ONYXIA_BASE_URL", raising=False)
    monkeypatch.delenv("OPENCODE_ONYXIA_API_KEY", raising=False)
    r2 = check_opencode("onyxia/m")
    assert not r2.ok and "manquants" in r2.message


def test_opencode_model_forms_and_other_providers():
    assert not check_opencode("sans-fournisseur").ok
    r = check_opencode("autre/modele", base_url="http://127.0.0.1:9/v1", api_key="k")
    assert r.ok and r.skipped                       # fournisseur inconnu du harnais : pas bloque


def test_opencode_model_list_shapes(models_server):
    _Models.body = [{"id": "qwen3-8-27b"}]                # liste nue au lieu de {"data": [...]}
    assert check_opencode("onyxia/qwen3-8-27b", base_url=models_server, api_key="k").ok


def test_run_preflight_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "check_claude", lambda m: preflight.PreflightResult(True, f"c:{m}"))
    monkeypatch.setattr(preflight, "check_opencode", lambda m: preflight.PreflightResult(True, f"o:{m}"))
    assert run_preflight("claude", "x").message == "c:x"
    assert run_preflight("opencode", "y").message == "o:y"


# ------------------------------------------------------------------ mauvais agent pour ce modele


@pytest.mark.parametrize("agent, model, needle", [
    ("opencode", "opus", "--agent claude"),                       # le cas du run reel
    ("opencode", "claude-opus-5-5", "--agent claude"),
    ("opencode", "sonnet", "--agent claude"),
    ("claude", "onyxia/qwen3-8-27b", "--agent opencode"),
])
def test_agent_model_mismatch_is_caught_without_any_call(agent, model, needle, monkeypatch):
    monkeypatch.setattr(preflight, "check_claude", lambda m: pytest.fail("appel inutile"))
    monkeypatch.setattr(preflight, "check_opencode", lambda m: pytest.fail("appel inutile"))
    r = run_preflight(agent, model)
    assert not r.ok and needle in r.message and f"`{model}`" in r.message


@pytest.mark.parametrize("agent, model", [("opencode", "onyxia/qwen3-8-27b"),
                                          ("opencode", "onyxia/opus-like"),
                                          ("claude", "claude-opus-5-5"), ("claude", "opus")])
def test_consistent_agent_and_model_are_not_flagged(agent, model):
    assert preflight.agent_mismatch(agent, model) == ""
