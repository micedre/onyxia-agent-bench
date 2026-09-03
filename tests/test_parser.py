"""Le parseur doit lire le vrai format nd-JSON d'opencode (`part` imbrique)."""
from pathlib import Path

from bench.opencode_driver import parse_output

SAMPLE = Path(__file__).parent / "resources" / "opencode_run_sample.ndjson"


def test_real_ndjson_sample():
    t = parse_output(SAMPLE.read_text(encoding="utf-8"))
    assert t.assistant_turns > 0
    assert t.tokens_in > 0 and t.tokens_total > t.tokens_in
    assert t.context_tokens_first > 0 and t.context_tokens_last >= t.context_tokens_first
    assert t.message_events, "le texte assistant doit produire des evenements message"
    assert t.tool_events and t.tool_events[0].name in ("bash", "read", "write", "edit", "glob", "grep")
    assert t.tool_events[0].text.startswith("{")  # input serialise
    assert all(e.turn >= 1 for e in t.events)
    # le texte du transcript est le texte ASSISTANT, pas le stdout brut
    assert not t.text.lstrip().startswith('{"type"')
    assert t.text.strip()


def test_permission_rejection_counted():
    line = ('{"type":"tool_use","part":{"type":"tool","tool":"bash","state":{"status":"error",'
            '"input":{"command":"rm -rf x"},"error":"The user rejected permission to use this specific tool call."}}}')
    t = parse_output(line)
    assert t.permission_rejections == 1 and t.tool_errors == 1
    assert t.tool_events[0].name == "bash"


def test_flat_fallback_and_plain_text():
    t = parse_output('{"role":"assistant","text":"bonjour","usage":{"input":10,"output":5}}')
    assert t.text == "bonjour" and t.tokens_in == 10 and t.tokens_out == 5
    t2 = parse_output("juste du texte")
    assert t2.text == "juste du texte" and t2.tokens_total == 0
    assert parse_output("").events == []
