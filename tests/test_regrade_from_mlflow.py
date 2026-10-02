"""scripts/regrade_from_mlflow.py : reconstruction d'un run depuis MLflow (client simule), controle des
totaux Claude sur les vrais flux, comparaison avant/apres."""
import importlib.util
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("regrade_from_mlflow", REPO / "scripts/regrade_from_mlflow.py")
rfm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rfm)

RES = Path(__file__).parent / "resources"


class StubClient:
    """Imite les trois appels de MlflowClient utilises, sur des artefacts en memoire."""

    def __init__(self, parent_id, cells, artifacts):
        self.parent_id, self.cells, self.artifacts = parent_id, cells, artifacts   # cells: [(run_id, nom)]
        self.writes = 0

    def search_runs(self, experiment_ids, filter_string="", max_results=100, **kw):
        assert f"tags.mlflow.parentRunId = '{self.parent_id}'" in filter_string
        return [SimpleNamespace(info=SimpleNamespace(run_id=rid, run_name=name)) for rid, name in self.cells]

    def download_artifacts(self, run_id, path, dst_path):
        data = self.artifacts[(run_id, path)]           # KeyError = artefact absent, comme MLflow
        out = Path(dst_path) / path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return str(out)


def _ws_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(f"ws/{name}", text)
    return buf.getvalue()


@pytest.mark.parametrize("name, expected", [
    ("t09_secret_trap/C4/seed3", ("t09_secret_trap", "C4", 3)),
    ("t10_diag_403/C0/seed12", ("t10_diag_403", "C0", 12)),
    ("onyxia-agent-bench-6m22h", None), ("a/b", None), ("a/b/seedX", None), ("a/b/c", None),
])
def test_parse_cell_name(name, expected):
    assert rfm.parse_cell_name(name) == expected


def test_fetch_cells_rebuilds_the_run_layout_and_reports_unreadable_cells(tmp_path):
    arts = {
        ("r1", "t09_secret_trap/C0/seed0/raw.ndjson"): b'{"type":"result"}\n',
        ("r1", "t09_secret_trap/C0/seed0/ws.zip"): _ws_zip({"analyse.md": "x", ".git/HEAD": "ref"}),
        ("r2", "t10_diag_403/C4/seed1/raw.ndjson"): b"{}\n",
        # r2 : pas de ws.zip -> illisible, ne doit pas arreter les autres
    }
    client = StubClient("P", [("r1", "t09_secret_trap/C0/seed0"), ("r2", "t10_diag_403/C4/seed1"),
                              ("rp", "onyxia-agent-bench-6m22h")], arts)
    got = rfm.fetch_cells(client, "P", tmp_path, ["1"])
    assert got["cells"] == 1 and len(got["missing"]) == 1 and "t10_diag_403/C4/seed1" in got["missing"][0]
    cell = tmp_path / "t09_secret_trap/C0/seed0"
    assert (cell / "raw.ndjson").read_text() == '{"type":"result"}\n'
    assert (cell / "ws/analyse.md").read_text() == "x" and (cell / "ws/.git/HEAD").is_file()
    assert not (cell / "_dl").exists()                         # pas de dechets de telechargement


def test_fetch_summary(tmp_path):
    client = StubClient("P", [], {("P", "summary/summary.json"): json.dumps({"meta": {"agent": "claude"}}).encode()})
    assert rfm.fetch_summary(client, "P", tmp_path) == {"meta": {"agent": "claude"}}
    assert (tmp_path / "summary.json").is_file()


def test_verify_claude_totals_on_the_real_streams():
    assert rfm.verify_claude_totals((RES / "claude_stream_success.ndjson").read_text(encoding="utf-8")) is None
    # flux sans tokens (limite de session) ou sans `result` : rien a verifier
    assert rfm.verify_claude_totals((RES / "claude_stream_session_limit.ndjson").read_text(encoding="utf-8")) is None
    assert rfm.verify_claude_totals('{"type":"assistant","message":{"id":"m","usage":{}}}') is None


def test_verify_claude_totals_checks_the_per_message_path_not_the_result_event(monkeypatch):
    """Le parseur prend ses totaux dans `result` quand il y est : les comparer a `result` ne prouverait
    rien. Le controle retire donc `result` et verifie la somme par message ; si elle regressait vers le
    double comptage (un evenement par bloc), il le dirait."""
    import bench.claude_driver as cd
    raw = (RES / "claude_stream_success.ndjson").read_text(encoding="utf-8")
    seen = {}
    real = cd.parse_claude_stream

    def spy(text):
        seen["has_result"] = '"type": "result"' in text or '"type":"result"' in text
        t = real(text)
        t.tokens_cache_read *= 2                      # double comptage simule
        return t
    monkeypatch.setattr(cd, "parse_claude_stream", spy)
    msg = rfm.verify_claude_totals(raw)
    assert seen["has_result"] is False                # le chemin de repli est bien celui qui est teste
    assert msg and "tokens_cache_read" in msg and "(244502, 122251)" in msg


def test_turn_gap_is_informational():
    raw = (RES / "claude_stream_success.ndjson").read_text(encoding="utf-8")
    assert rfm.turn_gap(raw) == (5, 5)
    assert rfm.turn_gap('{"type":"assistant","message":{"id":"m","usage":{}}}') is None


def _rec(task, cfg, seed, combined, tokens=100, turns=2, status="ok"):
    return {"task": task, "config": cfg, "seed": seed, "status": status, "tokens_total": tokens,
            "assistant_turns": turns, "axis_scores": {"combined": combined}}


def test_compare_flags_only_the_cells_whose_score_changed():
    old = {"records": [_rec("t10", "C0", 0, 0.8, 200, 10), _rec("t03", "C0", 0, 1.0, 200, 10)],
           "delta_combined_paired": {"mean": -0.1}}
    new = {"records": [_rec("t10", "C0", 0, 0.9, 100, 5), _rec("t03", "C0", 0, 1.0, 100, 5)],
           "delta_combined_paired": {"mean": -0.05}}
    d = rfm.compare(old, new)
    assert d["combined_changed"] == {"t10": [("C0", 0, 0.8, 0.9)]}        # t03 inchange
    assert d["cost"]["C0"]["tokens_total"] == (200.0, 100.0) and d["cost"]["C0"]["assistant_turns"] == (10.0, 5.0)
    assert d["paired_old"]["mean"] == -0.1 and d["paired_new"]["mean"] == -0.05
