"""Tests des helpers de notation sur des workspaces 'golden' (bon / mauvais)."""

from bench import grading as g
from bench.schema import Event, Transcript

PY_GOOD = 'import os\nprint(os.environ["AWS_S3_ENDPOINT"])\n'


def test_deliverables_exclude_tests_layer_and_scratch(make_ws):
    ws = make_ws({"main.py": "print(1)\n", "tests/test_main.py": "def test_x(): pass\n",
                  "conftest.py": "", "_scratch.py": "x=1\n",
                  ".opencode/skills/s/scripts/tool.py": "print('skill')\n"},
                 layer={"AGENTS.md": "# agents\n"})
    names = sorted(str(p.relative_to(ws)) for p in g.deliverable_files(ws, ["*.py"]))
    assert names == ["main.py"]
    assert [str(p.relative_to(ws)) for p in g.test_files(ws)] == ["tests/test_main.py"]
    assert g.file_exists(ws, ["*.py"], name="s").detail == "main.py"


def test_file_exists_not_satisfied_by_test_file(make_ws):
    ws = make_ws({"tests/test_x.py": "def test_a(): assert 1\n"})
    assert not g.file_exists(ws, ["*.py"], name="script").passed


def test_modified_layer_asset_not_a_deliverable(make_ws):
    ws = make_ws({".opencode/skills/argo/assets/wf.yaml": "apiVersion: argoproj.io/v1alpha1\nkind: Workflow\n# edited\n"},
                 layer={".opencode/skills/argo/assets/wf.yaml": "apiVersion: argoproj.io/v1alpha1\nkind: Workflow\n"})
    assert g.deliverable_files(ws, ["*.yaml"]) == []


def test_absence_checks_are_neutral_on_empty():
    for fn in (g.no_hardcoded_secrets, g.no_download_to_disk):
        c = fn("")
        assert c.axis == "skipped" and c.passed and c.score == 0.0
    assert g.no_hardcoded_secrets(PY_GOOD).passed


def test_secret_detection_and_placeholders():
    assert not g.no_hardcoded_secrets('password = "hunter2hunter2"\n').passed
    assert not g.no_hardcoded_secrets('key="AKIAABCDEFGHIJKLMNOP"\n').passed
    assert g.no_hardcoded_secrets('aws_secret_access_key="test"\n').passed  # mock moto
    assert g.no_hardcoded_secrets('token = os.getenv("TOKEN")\n').passed
    assert g.no_hardcoded_secrets("secretKey: ${AWS_SECRET_ACCESS_KEY}\n").passed


def test_download_patterns():
    assert not g.no_download_to_disk('s3.download_file("b", "k", "/tmp/x")\n').passed
    assert g.no_download_to_disk("# never wget in prod\nimport duckdb\n").passed
    assert not g.no_download_to_disk("wget https://x/y.parquet\n").passed


def test_env_vars_used_requires_a_read():
    assert not g.env_vars_used("# MLFLOW_TRACKING_URI est deja defini\n", ["MLFLOW_TRACKING_URI"]).passed
    assert g.env_vars_used('uri = os.environ["MLFLOW_TRACKING_URI"]\n', ["MLFLOW_TRACKING_URI"]).passed
    assert g.env_vars_used('Sys.getenv("AWS_S3_ENDPOINT")', ["AWS_S3_ENDPOINT"]).passed
    assert g.env_vars_used("endpoint: ${AWS_S3_ENDPOINT}", ["AWS_S3_ENDPOINT"]).passed


def test_gitignore_blocks_uses_git_semantics(make_ws):
    ws = make_ws({".gitignore": "data/\n.env\n# renv/library commented\n"})
    assert g.gitignore_blocks(ws, ["data/x.csv", ".env"]).passed
    c = g.gitignore_blocks(ws, ["renv/library/x"])
    assert not c.passed and c.score == 0.5
    ws2 = make_ws({"renv/.gitignore": "library/\nstaging/\n"})
    assert g.gitignore_blocks(ws2, ["renv/library/x"]).passed
    assert g.gitignore_blocks(make_ws({"a.py": ""}), [".env"]).detail == "pas de .gitignore"


def test_python_runs_in_project_env(make_ws):
    ws = make_ws({"main.py": "import yaml\nprint('ok')\n"})
    c = g.python_runs(ws, ["*.py"])
    assert c.passed, c.detail
    ws2 = make_ws({"main.py": "raise SystemExit(3)\n"})
    assert not g.python_runs(ws2, ["*.py"]).passed


def test_pick_entry_script_prefers_main(make_ws):
    ws = make_ws({"a_helper.py": "x = 1\n", "run.py": "if __name__ == '__main__':\n    print(1)\n"})
    scripts = g.deliverable_files(ws, ["*.py"])
    assert g._pick_entry_script(ws, scripts).name == "run.py"


def test_pytest_passes_without_pyproject(make_ws):
    ws = make_ws({"mod.py": "def f():\n    return 2\n",
                  "tests/test_mod.py": "from mod import f\ndef test_f():\n    assert f() == 2\n"})
    c = g.pytest_passes(ws)
    assert c.passed, c.detail
    assert g.pytest_passes(make_ws({"a.py": ""})).detail == "aucun test present"


def test_mlflow_helpers():
    good = 'mlflow.set_experiment("x")\nwith mlflow.start_run():\n    mlflow.log_param("a", 1)\n    mlflow.log_metric("m", 2)\n'
    assert g.references_mlflow_api(good).passed and g.mlflow_tracking_not_local(good).passed
    assert not g.mlflow_tracking_not_local('mlflow.set_tracking_uri("file:./mlruns")\n').passed
    assert not g.mlflow_tracking_not_local('mlflow.set_tracking_uri("sqlite:///mlflow.db")\n').passed


def test_vault_api_not_satisfied_by_env_var_alone():
    assert not g.references_vault_api('addr = os.environ["VAULT_ADDR"]\n').passed
    assert g.references_vault_api("client = hvac.Client(url=addr, token=tok)\n").passed


def test_git_helpers(make_ws):
    ws = make_ws({"analyse.md": "# a\n", ".env": "SECRET=abcdefgh\n"}, fixtures={"notes.md": "n\n"},
                 commit_agent=True)
    assert g.git_new_commit_made(ws).passed
    assert g.file_committed(ws, "analyse.md", name="w").passed
    assert not g.secret_not_committed(ws, ".env").passed  # tout a ete commite, .env compris
    ws2 = make_ws({"analyse.md": "# a\n"}, fixtures={"notes.md": "n\n"})
    assert g.secret_not_committed(ws2, ".env").axis == "skipped"  # rien commite


def test_transcript_helpers_use_assistant_text_only():
    t = Transcript(text="Le jeton S3 a expire.", events=[
        Event("tool", name="read", text='{"filePath": "troubleshooting.md"}',
              output="403 = token expire", turn=1),
        Event("message", text="Je regarde le log.", turn=1),
        Event("message", text="Le jeton S3 a expire.", turn=3),
    ])
    assert g.first_match_turn(t, [r"jeton.*expir"]) == 3
    assert g.first_match_turn(t, [r"iam"]) is None


def test_yaml_documents(make_ws):
    ws = make_ws({"wf.yaml": "apiVersion: argoproj.io/v1alpha1\nkind: Workflow\n---\nkind: Secret\n",
                  "bad.yaml": "a: [unclosed\n"})
    docs = g.yaml_documents(ws)
    assert [d.get("kind") for _, d in docs] == ["Workflow", "Secret"]
