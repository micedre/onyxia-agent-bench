"""Decouverte automatique des tasks (tasks/<id>/task.yaml + grade.py)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

from bench.schema import TaskSpec


def _load_grade_fn(task_dir: Path):
    grade_path = task_dir / "grade.py"
    if not grade_path.exists():
        raise FileNotFoundError(f"grade.py manquant dans {task_dir}")
    mod_name = f"bench_task_{task_dir.name}"
    spec = importlib.util.spec_from_file_location(mod_name, grade_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    if not hasattr(module, "grade"):
        raise AttributeError(f"{grade_path} doit exposer une fonction grade(ctx)")
    return module.grade


def discover_tasks(tasks_dir: Path) -> dict[str, TaskSpec]:
    tasks: dict[str, TaskSpec] = {}
    for d in sorted(p for p in tasks_dir.iterdir() if p.is_dir()):
        meta_path = d / "task.yaml"
        if not meta_path.exists():
            continue
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        tid = meta.get("id", d.name)
        tasks[tid] = TaskSpec(
            id=tid,
            prompt=meta["prompt"],
            dir=d,
            grade_fn=_load_grade_fn(d),
            timeout_s=int(meta.get("timeout_s", 900)),
            tags=list(meta.get("tags", [])),
            budget_tokens=int(meta["budget_tokens"]) if meta.get("budget_tokens") else None,
            budget_s=int(meta["budget_s"]) if meta.get("budget_s") else None,
            model=meta.get("model") or None,
            suite=str(meta.get("suite") or "model"),
        )
    return tasks
