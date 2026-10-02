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
    assert t.text == "reponse finale"
    assert t.assistant_turns == 1          # un message de sous-agent n'est pas un tour de l'agent principal


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


# ------------------------------------------------------------------ vrais flux de cellules (v2.1.286)

SUCCESS = Path(__file__).parent / "resources" / "claude_stream_success.ndjson"
LIMIT = Path(__file__).parent / "resources" / "claude_stream_session_limit.ndjson"


def _events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("{")]


def test_real_success_stream_matches_claudes_own_totals():
    """Vraie cellule reussie (t23/C4, Opus). Claude Code emet un evenement `assistant` par bloc de
    contenu : compter chaque evenement doublait tours et tokens de cache (9 tours au lieu de 5, cache
    lu 232 694 au lieu de 122 251, output 70 au lieu de 6 101). Les totaux de l'evenement `result`
    font foi."""
    raw = SUCCESS.read_text(encoding="utf-8")
    ev = _events(SUCCESS)
    res = next(e for e in reversed(ev) if e["type"] == "result")
    usage = res["usage"]
    t = parse_claude_stream(raw)
    assert t.assistant_turns == res["num_turns"] == 5
    assert t.tokens_cache_read == usage["cache_read_input_tokens"] == 122251
    assert t.tokens_cache_write == usage["cache_creation_input_tokens"] == 20449
    assert t.tokens_out == usage["output_tokens"] == 6101
    assert t.tokens_in == (usage["input_tokens"] + usage["cache_read_input_tokens"]
                           + usage["cache_creation_input_tokens"]) == 142710
    assert t.tokens_total == 142710 + 6101
    assert t.cost == res["total_cost_usd"] == 0.3101022
    assert t.text.strip() and t.permission_rejections == 0


def test_real_success_stream_blocks_of_one_message_share_a_turn():
    ev = [e for e in _events(SUCCESS) if e["type"] == "assistant"]
    ids = [e["message"]["id"] for e in ev]
    assert len(ids) > len(set(ids)) == 5          # le piege est bien dans ce flux : 9 evenements, 5 tours
    t = parse_claude_stream(SUCCESS.read_text(encoding="utf-8"))
    turns = {e.turn for e in t.events}
    assert turns <= set(range(1, 6)) and max(turns) <= t.assistant_turns
    # la reponse finale (texte) est dans le DERNIER tour, pas au tour 9
    assert t.message_events[-1].turn == t.assistant_turns == 5
    first_seen = {}
    for e in ev:
        first_seen.setdefault(e["message"]["id"], len(first_seen) + 1)
    for e in t.events:
        assert e.turn == first_seen[e.raw["message"]["id"]]


def test_real_session_limit_stream():
    """La vraie sortie d'une cellule qui a atteint la limite de session de l'abonnement."""
    from bench.schema import RunResult, cell_status
    t = parse_claude_stream(LIMIT.read_text(encoding="utf-8"))
    assert "You've hit your session limit" in t.text and "resets" in t.text
    assert t.tokens_total == 0 and t.assistant_turns == 1
    run = RunResult("t", "C0", "m", 0, Path("."), t, exit_code=1, error="exit=1")
    assert cell_status(run) == "error"            # exclue des moyennes, pas un resultat de l'agent


def _asst(mid, blocks, usage, **extra):
    return json.dumps({"type": "assistant", "message": {"id": mid, "usage": usage, "content": blocks},
                       **extra})


def test_blocks_of_one_message_count_once_without_a_result_event():
    """Cellule tuee avant `result` : on somme le DERNIER instantane de chaque message, une fois."""
    u = {"input_tokens": 2, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 10,
         "output_tokens": 7}
    raw = "\n".join([
        _asst("m1", [{"type": "thinking", "thinking": "..."}], u),
        _asst("m1", [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}], u),
        _asst("m2", [{"type": "text", "text": "fini"}], u)])
    t = parse_claude_stream(raw)
    assert t.assistant_turns == 2 and t.tokens_cache_read == 200 and t.tokens_in == 2 * 112
    assert t.tool_events[0].turn == 1 and t.message_events[0].turn == 2


def test_model_usage_includes_subagents_and_wins_over_usage():
    """Verifie sur de vraies cellules avec sous-agents (6 sur 33) : `result.usage` ne couvre que l'agent
    principal alors que `modelUsage` (qui fonde le cout) les inclut ; prendre `usage` sous-estimait le cout
    de C4, qui delegue, de ~25 %."""
    u = {"input_tokens": 1, "cache_read_input_tokens": 1, "output_tokens": 1}
    raw = "\n".join([_asst("m1", [{"type": "text", "text": "a"}], u),
                     json.dumps({"type": "result", "num_turns": 1, "total_cost_usd": 0.9,
                                 "usage": {"input_tokens": 3, "cache_read_input_tokens": 400,
                                           "cache_creation_input_tokens": 50, "output_tokens": 900},
                                 "modelUsage": {
                                     "claude-opus-5-5": {"inputTokens": 3, "cacheReadInputTokens": 400,
                                                         "cacheCreationInputTokens": 50, "outputTokens": 900},
                                     "claude-haiku-4-5": {"inputTokens": 2, "cacheReadInputTokens": 100,
                                                          "cacheCreationInputTokens": 10, "outputTokens": 70}}})])
    t = parse_claude_stream(raw)
    assert (t.tokens_cache_read, t.tokens_cache_write, t.tokens_out) == (500, 60, 970)
    assert t.tokens_in == 5 + 500 + 60 and t.cost == 0.9


def test_real_success_stream_model_usage_equals_usage_without_subagents():
    res = next(e for e in reversed(_events(SUCCESS)) if e["type"] == "result")
    mu = next(iter(res["modelUsage"].values()))
    assert mu["cacheReadInputTokens"] == res["usage"]["cache_read_input_tokens"]
    assert mu["outputTokens"] == res["usage"]["output_tokens"]


def test_result_usage_wins_over_event_snapshots():
    u = {"input_tokens": 1, "cache_read_input_tokens": 1, "output_tokens": 1}
    raw = "\n".join([_asst("m1", [{"type": "text", "text": "a"}], u),
                     json.dumps({"type": "result", "num_turns": 1, "total_cost_usd": 0.5,
                                 "usage": {"input_tokens": 3, "cache_read_input_tokens": 40,
                                           "cache_creation_input_tokens": 5, "output_tokens": 900}})])
    t = parse_claude_stream(raw)
    assert (t.tokens_in, t.tokens_out, t.tokens_cache_read, t.tokens_cache_write) == (48, 900, 40, 5)


def test_context_size_ignores_subagent_messages_and_repeated_blocks():
    big = {"input_tokens": 1, "cache_read_input_tokens": 5000}
    small = {"input_tokens": 1, "cache_read_input_tokens": 100}
    raw = "\n".join([
        _asst("m1", [{"type": "text", "text": "a"}], small),
        _asst("m1", [{"type": "tool_use", "id": "t", "name": "Task", "input": {}}], small),
        _asst("s1", [{"type": "text", "text": "sous-agent"}], big, parent_tool_use_id="t"),
        _asst("m2", [{"type": "text", "text": "b"}], {"input_tokens": 1, "cache_read_input_tokens": 300})])
    t = parse_claude_stream(raw)
    assert t.assistant_turns == 2 and t.subagent_calls == 1          # m1 et m2 : le sous-agent n'en est pas un
    assert (t.context_tokens_first, t.context_tokens_last) == (101, 301)   # sous-agent exclu
    assert "sous-agent" not in t.text


def test_subagent_tools_belong_to_the_turn_that_launched_them():
    """Comme chez OpenCode ou la session enfant n'apparait pas dans le flux du parent : le travail d'un
    sous-agent se passe DANS le tour de l'agent principal qui l'a lance, et ne decale pas les tours
    suivants (le grader de t10 lit ces numeros)."""
    u = {"input_tokens": 1}
    raw = "\n".join([
        _asst("m1", [{"type": "text", "text": "je delegue"}], u),
        _asst("m2", [{"type": "tool_use", "id": "ta", "name": "Agent", "input": {}}], u),
        _asst("s1", [{"type": "tool_use", "id": "ts1", "name": "Bash", "input": {}}], u, parent_tool_use_id="ta"),
        _asst("s2", [{"type": "tool_use", "id": "ts2", "name": "Read", "input": {}}], u, parent_tool_use_id="ta"),
        _asst("m3", [{"type": "text", "text": "fini"}], u)])
    t = parse_claude_stream(raw)
    assert t.assistant_turns == 3
    by_name = {e.name: e.turn for e in t.tool_events}
    assert by_name == {"Agent": 2, "Bash": 2, "Read": 2}               # le travail du sous-agent est au tour 2
    assert [e.turn for e in t.message_events] == [1, 3]                 # m3 reste le tour 3, pas le 5
