"""Types de base du harnais de benchmark."""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass
class TaskSpec:
    id: str
    prompt: str
    dir: Path
    grade_fn: Callable[["GradeContext"], list["Check"]]
    timeout_s: int = 900
    tags: list[str] = field(default_factory=list)


@dataclass
class ConfigSpec:
    id: str
    layers: list[str]


@dataclass
class Event:
    """Un evenement ordonne du transcript (message assistant ou appel d'outil)."""
    type: str            # "message" | "tool"
    text: str = ""       # texte du message, ou resume de l'appel d'outil
    name: str = ""       # nom de l'outil (si type == "tool")
    raw: dict = field(default_factory=dict)


@dataclass
class Transcript:
    text: str = ""                                   # texte assistant concatene
    events: list[Event] = field(default_factory=list)
    raw_stdout: str = ""
    tokens_in: int = 0
    tokens_out: int = 0

    @property
    def tool_events(self) -> list[Event]:
        return [e for e in self.events if e.type == "tool"]

    @property
    def tokens_total(self) -> int:
        return self.tokens_in + self.tokens_out


@dataclass
class RunResult:
    task_id: str
    config_id: str
    model: str
    seed: int
    workspace: Path
    transcript: Transcript
    exit_code: int = 0
    timed_out: bool = False
    wall_clock_s: float = 0.0
    files_changed: list[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class Check:
    name: str
    passed: bool
    score: float = 0.0          # 0..1
    weight: float = 1.0
    axis: str = "functional"    # functional | platform | repro | safety | efficiency
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "passed": self.passed, "score": round(self.score, 4),
            "weight": self.weight, "axis": self.axis, "detail": self.detail,
        }


AXES = ["functional", "platform", "repro", "safety", "efficiency"]


def combined_score(axis_scores: dict[str, Optional[float]]) -> Optional[float]:
    """Moyenne non ponderee des axes effectivement mesures (valeurs non None)."""
    vals = [v for v in axis_scores.values() if v is not None]
    return round(statistics.fmean(vals), 4) if vals else None


@dataclass
class GradeReport:
    checks: list[Check] = field(default_factory=list)

    def axis_score(self, axis: str) -> Optional[float]:
        items = [c for c in self.checks if c.axis == axis]
        if not items:
            return None
        w = sum(c.weight for c in items) or 1.0
        return sum(c.score * c.weight for c in items) / w

    @property
    def safety_violations(self) -> int:
        return sum(1 for c in self.checks if c.axis == "safety" and not c.passed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks": [c.to_dict() for c in self.checks],
            "axis_scores": {a: self.axis_score(a) for a in AXES},
            "safety_violations": self.safety_violations,
        }
