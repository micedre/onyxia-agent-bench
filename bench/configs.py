"""Chargement de l'echelle d'ablation et materialisation d'une config dans un workspace."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from bench.schema import ConfigSpec


def load_ladder(configs_dir: Path) -> tuple[str, dict[str, ConfigSpec]]:
    data = yaml.safe_load((configs_dir / "ladder.yaml").read_text(encoding="utf-8"))
    base = data["base"]
    configs = {cid: ConfigSpec(cid, layers or [])
               for cid, layers in data["configs"].items()}
    return base, configs


def _deep_merge(a: dict, b: dict) -> dict:
    """Fusionne b dans a (recursif pour les dicts) et renvoie a."""
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            _deep_merge(a[k], v)
        else:
            a[k] = v
    return a


def _copy_tree(src: Path, dst: Path):
    for p in src.rglob("*"):
        rel = p.relative_to(src)
        target = dst / rel
        if p.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)


def materialize(config: ConfigSpec, configs_dir: Path, base: str, workspace: Path) -> Path:
    """Assemble opencode.json (base + patches) et copie les fichiers des couches.

    Renvoie le chemin d'opencode.json.
    """
    base_cfg_path = configs_dir / base / "opencode.json"
    cfg = json.loads(base_cfg_path.read_text(encoding="utf-8"))

    for layer in config.layers:
        ldir = configs_dir / "layers" / layer
        root = ldir / "root"
        if root.is_dir():
            _copy_tree(root, workspace)
        patch = ldir / "opencode.patch.json"
        if patch.exists():
            _deep_merge(cfg, json.loads(patch.read_text(encoding="utf-8")))

    out = workspace / "opencode.json"
    out.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return out
