import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _git(ws: Path, *args):
    subprocess.run(["git", "-C", str(ws), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   check=True, capture_output=True)


@pytest.fixture
def make_ws(tmp_path):
    """Cree un workspace git : `fixtures` commites, `files` ecrits apres (non suivis), comme
    runner._init_workspace + un agent qui a produit des fichiers."""
    counter = iter(range(10_000))

    def _make(files: dict[str, str], fixtures: dict[str, str] | None = None,
              commit_agent: bool = False, layer: dict[str, str] | None = None) -> Path:
        ws = tmp_path / f"cell{next(counter)}" / "ws"
        ws.mkdir(parents=True)
        for rel, content in (fixtures or {}).items():
            p = ws / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        _git(ws, "init", "-q")
        _git(ws, "add", "-A")
        _git(ws, "commit", "-q", "-m", "fixtures", "--allow-empty")
        if layer:
            import hashlib
            import json
            manifest = {}
            for rel, content in layer.items():
                p = ws / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
                manifest[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
            (ws / ".bench_layer_files.json").write_text(json.dumps(manifest))
        for rel, content in files.items():
            p = ws / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        if commit_agent:
            _git(ws, "add", "-A")
            _git(ws, "commit", "-q", "-m", "agent work")
        return ws
    return _make
