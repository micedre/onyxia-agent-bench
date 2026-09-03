"""Types de base du harnais de benchmark."""
from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TaskSpec:
    id: str
    prompt: str
    dir: Path
    grade_fn: Callable[[GradeContext], list[Check]]
    timeout_s: int = 900
    tags: list[str] = field(default_factory=list)
    # Budgets pour l'axe efficiency (voir runner.efficiency_checks). None = defaut harnais.
    budget_tokens: int | None = None
    budget_s: int | None = None
    # Modele impose pour cette tache (ex. un modele vision pour t11) ; None = --model du run.
    model: str | None = None


@dataclass
class ConfigSpec:
    id: str
    layers: list[str]


@dataclass
class Event:
    """Un evenement ordonne du transcript (message assistant ou appel d'outil)."""
    type: str            # "message" | "tool"
    text: str = ""       # texte du message, ou resume de l'appel d'outil (input serialise)
    name: str = ""       # nom de l'outil (si type == "tool")
    raw: dict = field(default_factory=dict)
    # renseignes pour type == "tool" quand le format le permet
    status: str = ""     # completed | error | ...
    output: str = ""     # sortie de l'outil (tronquee)
    turn: int = 0        # numero du tour assistant (1-based) pendant lequel l'evenement a lieu


@dataclass
class Transcript:
    text: str = ""                                   # texte assistant concatene
    events: list[Event] = field(default_factory=list)
    raw_stdout: str = ""
    tokens_in: int = 0            # somme des tokens d'entree sur tous les tours (cout "facture")
    tokens_out: int = 0           # somme des tokens de sortie (hors raisonnement)
    tokens_reasoning: int = 0     # somme des tokens de raisonnement
    tokens_cache_read: int = 0
    tokens_cache_write: int = 0
    context_tokens_last: int = 0  # taille du contexte (input) au dernier tour = prompt final
    context_tokens_first: int = 0 # taille du contexte au premier tour = poids du prompt systeme
    cost: float = 0.0
    assistant_turns: int = 0      # nombre de tours LLM (step_finish)
    permission_rejections: int = 0
    tool_errors: int = 0
    subagent_calls: int = 0       # appels au tool `task` (delegation a un sous-agent)

    @property
    def tool_events(self) -> list[Event]:
        return [e for e in self.events if e.type == "tool"]

    @property
    def message_events(self) -> list[Event]:
        return [e for e in self.events if e.type == "message"]

    @property
    def tokens_total(self) -> int:
        return self.tokens_in + self.tokens_out + self.tokens_reasoning


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
    error: str | None = None
    # "ok" | "timeout" | "oom" | "never_ran" | "error" - voir cell_status()
    status: str = "ok"
    agent_s: float = 0.0   # duree de l appel agent seul (== wall_clock_s en mode process)


# Statuts de cellule. Seuls "ok" et "timeout" comptent dans les moyennes : dans les deux cas
# l'agent a effectivement tourne (un timeout est un echec de l'agent dans le budget imparti).
# "never_ran" (pod jamais pret, binaire absent...), "oom" et "error" sont des defaillances
# d'infrastructure/harnais : elles sont comptees a part (bloc "reliability") et exclues des
# moyennes, sinon un run dont le cluster sature en fin d'execution attribue des zeros a la
# derniere tache traitee, ce qui est arrive sur de vrais runs (t10 = 15/15 cellules perdues).
VALID_STATUSES = ("ok", "timeout")


def cell_status(run: RunResult) -> str:
    if run.error is None:
        return "ok"
    if run.timed_out or run.exit_code == 124 or "[TIMEOUT]" in (run.transcript.raw_stdout or ""):
        return "timeout"
    if run.exit_code == 137:
        return "oom"
    if not run.transcript.events and run.transcript.tokens_total == 0:
        return "never_ran"
    return "error"


@dataclass
class Check:
    name: str
    passed: bool
    score: float = 0.0          # 0..1
    weight: float = 1.0
    axis: str = "functional"    # functional | platform | repro | safety | efficiency | skipped
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "passed": self.passed, "score": round(self.score, 4),
            "weight": self.weight, "axis": self.axis, "detail": self.detail,
        }


AXES = ["functional", "platform", "repro", "safety", "efficiency"]


def skipped(name: str, detail: str) -> Check:
    """Check neutre : n'entre dans aucun axe (ex. verification d'absence sur un workspace vide,
    binaire de rendu absent). Evite qu'une cellule ou l'agent n'a rien produit obtienne
    safety=1.0 'par defaut'."""
    return Check(name, True, 0.0, axis="skipped", detail=detail)


def combined_score(axis_scores: dict[str, float | None]) -> float | None:
    """Moyenne non ponderee des axes effectivement mesures (valeurs non None)."""
    vals = [v for k, v in axis_scores.items() if v is not None and k in AXES]
    return round(statistics.fmean(vals), 4) if vals else None


@dataclass
class GradeReport:
    checks: list[Check] = field(default_factory=list)

    def axis_score(self, axis: str) -> float | None:
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
