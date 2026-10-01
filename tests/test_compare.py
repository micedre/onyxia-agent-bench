"""bench compare : apport apparie par run, ecarts entre runs, cout."""
import json

from bench.compare import load_run, render


def _rec(task, config, seed, combined, status="ok", **extra):
    return {"task": task, "config": config, "seed": seed, "status": status,
            "axis_scores": {"combined": combined}, "tokens_total": 1000,
            "assistant_turns": 5, "agent_s": 10.0, **extra}


def _run(tmp_path, name, agent, model, scores):
    recs = [_rec(t, c, s, v) for (t, c, s), v in scores.items()]
    d = tmp_path / name
    d.mkdir()
    (d / "summary.json").write_text(json.dumps(
        {"meta": {"agent": agent, "model": model}, "records": recs}))
    return load_run(d)


def test_compare_two_agents(tmp_path):
    grid = [(t, s) for t in ("t1", "t2", "t3") for s in range(3)]
    a = _run(tmp_path, "oc", "opencode", "m",
             {**{(t, "C0", s): 0.4 for t, s in grid}, **{(t, "C4", s): 0.8 for t, s in grid}})
    b = _run(tmp_path, "cl", "claude", "opus",
             {**{(t, "C0", s): 0.7 for t, s in grid}, **{(t, "C4", s): 0.9 for t, s in grid}})
    md = render([a, b])
    assert "opencode:m" in md and "claude:opus" in md
    assert "+0.40 [+0.40, +0.40] (n=9)" in md       # delta apparie du run opencode
    assert "+0.20 [+0.20, +0.20] (n=9)" in md       # delta apparie du run claude
    assert "+0.30 [+0.30, +0.30] (n=3)" in md       # claude C0 - opencode C0, par tache


def test_invalid_cells_excluded_and_labels_disambiguated(tmp_path):
    a = _run(tmp_path, "r1", "opencode", "m", {("t1", "C0", 0): 0.5, ("t1", "C4", 0): 0.9})
    b = _run(tmp_path, "r2", "opencode", "m", {("t1", "C0", 0): 0.5, ("t1", "C4", 0): 0.9})
    b["records"].append(_rec("t1", "C4", 1, 0.0, status="never_ran"))
    assert load_run(tmp_path / "r1")["records"] == a["records"]
    md = render([a, b])
    assert "(r1)" in md and "(r2)" in md
