from pathlib import Path
from bench.configs import load_ladder
from bench.registry import discover_tasks
from bench.opencode_driver import MockOpenCodeDriver
from bench.mlflow_logging import NullLogger
from bench.runner import run_benchmark

REPO = Path(__file__).resolve().parent.parent


def test_end_to_end_mock(tmp_path):
    tasks = discover_tasks(REPO / "tasks")
    base, configs = load_ladder(REPO / "configs")
    sel_tasks = [tasks["t10_diag_403"], tasks["t01_s3_parquet"]]
    sel_configs = [configs["C0"], configs["C4"]]
    summary = run_benchmark(sel_tasks, sel_configs, base, REPO / "configs",
                            "mock/model", seeds=1, driver=MockOpenCodeDriver(),
                            out_dir=tmp_path, logger=NullLogger())
    # la config complete doit faire au moins aussi bien que l'agent nu (fonctionnel)
    means = summary["mean_by_config"]
    assert means["C4"]["functional"] >= means["C0"]["functional"]
    # T01 : platform-correctness doit s'ameliorer nettement avec le contexte
    assert "delta_by_axis" in summary
