"""Chargement de l'echelle d'ablation et materialisation d'une config dans un workspace."""
from __future__ import annotations

import hashlib
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_tree(src: Path, dst: Path) -> dict[str, str]:
    """Copie src/ dans dst et renvoie {chemin relatif: sha256} des fichiers ecrits."""
    written: dict[str, str] = {}
    for p in src.rglob("*"):
        rel = p.relative_to(src)
        target = dst / rel
        if p.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
            written[rel.as_posix()] = _sha256(target)
    return written


# Nom du manifeste {chemin relatif: sha256} des fichiers deposes par les couches (root/ +
# opencode.json) - permet a bench/grading.py d'exclure ces fichiers de la notation *tant
# qu'ils sont intacts* : sans ca, un `file_exists`/`code_text` sur `*.py`/`*.yaml`/... matche
# aussi les scripts/manifestes de reference que les skills embarquent (ex. `.opencode/skills/
# eda-duckdb/scripts/profile_parquet.py`), et une cellule ou l'agent n'a RIEN produit peut
# noter functional=1.0 en lisant le fichier de la skill comme s'il etait la sortie de l'agent
# (constate sur de vrais runs). Le hash (plutot qu'une simple liste de chemins) permet de ne
# PAS exclure un fichier que l'agent aurait lui-meme modifie/complete - ca reste alors bien
# son travail.
LAYER_FILES_MANIFEST = ".bench_layer_files.json"

# Agents dont le modele n'est jamais ecrase par `model=` : "reviewer" doit rester sur un
# modele different de celui qu'il note (le but est un second avis independant, pas le
# modele qui se note lui-meme) ; "dataviz-vision" est fige sur un modele vision - lui
# imposer le modele texte teste par le run casserait sa seule raison d'etre.
MODEL_OVERRIDE_EXCLUDE_AGENTS = {"reviewer", "dataviz-vision"}


def materialize(config: ConfigSpec, configs_dir: Path, base: str, workspace: Path,
                model: str | None = None) -> Path:
    """Assemble opencode.json (base + patches) et copie les fichiers des couches.

    Si `model` est fourni, ecrase le "model" de premier niveau et celui de chaque agent
    (sauf `MODEL_OVERRIDE_EXCLUDE_AGENTS`) par cette valeur. Necessaire car `--model`/`-m`
    ne s'applique qu'a l'agent primaire invoque par `opencode run` : un agent delegue via
    le tool `task` (python-ds, r-ds, mlops) tourne sinon sur le modele fige dans sa propre
    entree "agent" de la config, quel que soit `--model` - ce qui casse la promesse "meme
    modele sur toute l'echelle" du benchmark des qu'on teste un modele different de celui
    code en dur dans les couches. Constate empiriquement : un agent `r-ds` delegue restait
    sur qwen3-6-35b-moe alors que `--model` demandait qwen3-8-27b pour l'agent `build`.

    Renvoie le chemin d'opencode.json. Ecrit aussi `LAYER_FILES_MANIFEST` a la racine du
    workspace : {chemin: sha256} des fichiers deposes par les couches (pas produits par
    l'agent), pour que la notation les exclue tant qu'ils restent inchanges.
    """
    base_cfg_path = configs_dir / base / "opencode.json"
    cfg = json.loads(base_cfg_path.read_text(encoding="utf-8"))

    layer_files: dict[str, str] = {}
    for layer in config.layers:
        ldir = configs_dir / "layers" / layer
        root = ldir / "root"
        if root.is_dir():
            layer_files.update(_copy_tree(root, workspace))
        patch = ldir / "opencode.patch.json"
        if patch.exists():
            _deep_merge(cfg, json.loads(patch.read_text(encoding="utf-8")))

    if model:
        if "model" in cfg:
            cfg["model"] = model
        for agent_name, agent_cfg in cfg.get("agent", {}).items():
            if agent_name in MODEL_OVERRIDE_EXCLUDE_AGENTS:
                continue
            if isinstance(agent_cfg, dict) and "model" in agent_cfg:
                agent_cfg["model"] = model

    out = workspace / "opencode.json"
    out.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    layer_files["opencode.json"] = _sha256(out)
    (workspace / LAYER_FILES_MANIFEST).write_text(
        json.dumps(layer_files, indent=2, sort_keys=True), encoding="utf-8")
    return out
