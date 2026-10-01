"""Agent Claude Code : parseur stream-json, traduction des couches, exclusion de la notation."""
import json
from pathlib import Path

from bench.agents import materialize, parse_transcript
from bench.claude_driver import build_command, parse_claude_stream
from bench.configs import load_ladder
from bench.grading import _is_layer_path
from bench.schema import ConfigSpec

CONFIGS = Path(__file__).resolve().parent.parent / "configs"


def _stream(*objs: dict) -> str:
    return "\n".join(json.dumps(o) for o in objs)


USAGE = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100,
         "cache_creation_input_tokens": 20}

STREAM = _stream(
    {"type": "system", "subtype": "init", "model": "claude-x"},
    {"type": "assistant", "message": {"usage": USAGE, "content": [
        {"type": "text", "text": "Je regarde."},
        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "a.txt", "is_error": False}]}},
    {"type": "assistant", "message": {"usage": USAGE, "content": [
        {"type": "tool_use", "id": "t2", "name": "Task", "input": {"prompt": "x"}},
        {"type": "tool_use", "id": "t3", "name": "Bash", "input": {"command": "rm a.txt"}}]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t3", "is_error": True,
         "content": [{"type": "text", "text": "permission denied"}]}]}},
    {"type": "assistant", "message": {"usage": USAGE, "content": [
        {"type": "text", "text": "Le jeton S3 a expire."}]}},
    {"type": "result", "subtype": "success", "is_error": False, "num_turns": 3,
     "total_cost_usd": 0.0123, "permission_denials": [{"tool_name": "Bash"}],
     "result": "Le jeton S3 a expire."},
)


def test_stream_parsing():
    t = parse_claude_stream("warning non json\n" + STREAM)
    assert t.assistant_turns == 3
    # tokens_in = input + cache_read + cache_creation, somme sur les tours
    assert t.tokens_in == 3 * 130 and t.tokens_out == 15 and t.tokens_cache_read == 300
    assert t.context_tokens_first == 130 and t.context_tokens_last == 130
    assert t.cost == 0.0123
    assert [e.name for e in t.tool_events] == ["Bash", "Task", "Bash"]
    assert t.tool_events[0].status == "completed" and t.tool_events[0].output == "a.txt"
    assert t.tool_events[2].status == "error" and "permission denied" in t.tool_events[2].output
    assert t.tool_errors == 1 and t.subagent_calls == 1 and t.permission_rejections == 1
    assert [e.turn for e in t.message_events] == [1, 3]
    # texte assistant uniquement : ni sortie d'outil ni stdout brut
    assert t.text == "Je regarde.\nLe jeton S3 a expire."


def test_subagent_messages_do_not_pollute_final_text():
    t = parse_claude_stream(_stream(
        {"type": "assistant", "parent_tool_use_id": "t9", "message": {"usage": USAGE, "content": [
            {"type": "text", "text": "reponse du sous-agent"}]}},
        {"type": "assistant", "message": {"usage": USAGE, "content": [
            {"type": "text", "text": "reponse finale"}]}}))
    assert t.text == "reponse finale" and t.assistant_turns == 2


def test_result_only_and_empty():
    t = parse_claude_stream(_stream({"type": "result", "result": "ok", "num_turns": 1}))
    assert t.text == "ok" and t.assistant_turns == 1
    assert parse_claude_stream("").events == []
    assert parse_transcript("claude", STREAM).assistant_turns == 3
    assert parse_transcript("opencode", "juste du texte").text == "juste du texte"


def test_command_is_isolated_and_headless():
    cmd = build_command("claude", "fais X", "claude-x")
    assert cmd[:3] == ["claude", "-p", "fais X"]
    assert "--setting-sources" in cmd and cmd[cmd.index("--setting-sources") + 1] == "project"
    assert "--strict-mcp-config" in cmd and "--verbose" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-x"


def _mat(tmp_path, config_id):
    _, configs = load_ladder(CONFIGS)
    materialize("claude", configs[config_id], CONFIGS, "c0_bare", tmp_path)
    return json.loads((tmp_path / ".claude" / "settings.json").read_text())["permissions"]


def test_c0_is_bare(tmp_path):
    perms = _mat(tmp_path, "C0")
    assert "Bash" in perms["allow"] and "WebFetch" in perms["deny"]
    assert not (tmp_path / "CLAUDE.md").exists()
    assert not (tmp_path / ".claude" / "skills").exists()
    assert not (tmp_path / ".claude" / "agents").exists()


def test_c4_translation(tmp_path):
    perms = _mat(tmp_path, "C4")
    claude_md = (tmp_path / "CLAUDE.md").read_text()
    assert "Onyxia" in claude_md                       # AGENTS.md
    assert "{file:" not in claude_md                   # prompt build resolu
    skills = list((tmp_path / ".claude" / "skills").glob("*/SKILL.md"))
    assert len(skills) >= 10
    agents = {p.stem for p in (tmp_path / ".claude" / "agents").glob("*.md")}
    assert {"python-ds", "r-ds", "mlops", "reviewer", "dataviz-vision"} <= agents
    assert "build" not in agents and "plan" not in agents
    rev = (tmp_path / ".claude" / "agents" / "reviewer.md").read_text()
    assert rev.startswith("---\nname: reviewer\n") and "tools: Read, Grep, Glob, Bash" in rev
    assert "{file:" not in rev
    # `*: ask` -> plus de Bash nu ; ask/deny nommes -> deny ; allow nommes conserves
    assert "Bash" not in perms["allow"]
    assert "Bash(git *)" in perms["allow"] and "Bash(uv *)" in perms["allow"]
    assert "Bash(git push*)" in perms["deny"] and "Bash(rm *)" in perms["deny"]
    assert "Skill" in perms["allow"] and "Task" in perms["allow"]
    assert not (tmp_path / ".opencode").exists() and not (tmp_path / "opencode.json").exists()


def test_layer_files_excluded_from_grading(tmp_path):
    _mat(tmp_path, "C4")
    manifest = json.loads((tmp_path / ".bench_layer_files.json").read_text())
    assert "CLAUDE.md" in manifest and ".claude/settings.json" in manifest
    assert any(k.startswith(".claude/skills/") for k in manifest)
    for rel in manifest:
        assert _is_layer_path(Path(rel)), rel
    assert _is_layer_path(Path("CLAUDE.md"))


def test_ladder_unchanged():
    _, configs = load_ladder(CONFIGS)
    assert configs["C0"].layers == [] and "guardrails" in configs["C4"].layers
    assert isinstance(configs["C4"], ConfigSpec)


REAL = Path(__file__).parent / "resources" / "claude_stream_auth_error.ndjson"


def test_real_stream_sample_auth_error():
    """Premiere sortie reelle de `claude -p --output-format stream-json` (v2.1.286, jeton OAuth
    invalide, CLAUDE_CONFIG_DIR vierge) : pas d'invite de connexion, une erreur 401 propre."""
    from bench.schema import RunResult, cell_status
    raw = REAL.read_text(encoding="utf-8")
    t = parse_claude_stream(raw)
    assert t.assistant_turns == 1 and t.tokens_total == 0
    assert "Failed to authenticate" in t.text and "401" in t.text
    assert t.permission_rejections == 0 and t.tool_errors == 0
    # un echec d'authentification n'est ni un resultat de l'agent ni un timeout : exclu des moyennes
    run = RunResult("t", "C0", "m", 0, Path("."), t, exit_code=1, error="exit=1")
    assert cell_status(run) == "error"
