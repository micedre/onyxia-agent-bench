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
    # Jeu de taches auquel elle appartient (cf. --suite).
    #   "context" : l'enonce TAIT la convention que les couches de config fournissent, donc la
    #               tache mesure l'apport du contexte. C'est le jeu par defaut.
    #   "model"   : la tache mesure la competence du modele (pandas, jointures, geo...) ou son
    #               enonce recite deja la convention - les deux configs y sont a egalite.
    # Mesure a l'appui (run qwen3-6-all, 17 taches x C0/C4 x 5 seeds, deltas apparies) :
    # +0.27 IC95 [+0.06, +0.48] sur les taches "context", -0.04 sur les autres, +0.04 non
    # significatif sur l'ensemble : melanger les deux noyait le signal.
    suite: str = "model"


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


# Statuts de cellule. Comptent dans les moyennes tous les cas ou L'AGENT A REELLEMENT TOURNE :
# "ok", "timeout" (echec dans le budget imparti) et "agent_error" (le CLI rend un code non nul
# mais l'agent a produit des tours et des tokens - typiquement opencode qui sort en 1 apres des
# rejets de permission automatiques, alors que le travail est complet et correct).
# Sont exclus "never_ran" (pod jamais pret, binaire absent, modele qui refuse l'appel d'outil :
# aucun tour, aucun token), "oom" et "error" : ce sont des defaillances d'infrastructure ou du
# harnais, pas des resultats d'agent.
#
# Les deux exclusions comptent autant l'une que l'autre. Compter une defaillance d'infra comme
# un echec de l'agent attribuait des zeros a la derniere tache d'un run ou le cluster saturait
# (t10 = 15/15 cellules perdues). Mais exclure une cellule complete parce que le CLI a rendu 1
# fait l'erreur symetrique : sur un run reel, une cellule C4 a 1.00 partout a ete ecartee apres
# huit rejets de permission, et comme ce sont les garde-fous de C4 qui provoquent ces rejets,
# l'exclusion penalisait justement la config mesuree (C4 publie a 0.750 au lieu de 0.800).
VALID_STATUSES = ("ok", "timeout", "agent_error")


def cell_status(run: RunResult) -> str:
    if run.error is None:
        return "ok"
    if run.timed_out or run.exit_code == 124 or "[TIMEOUT]" in (run.transcript.raw_stdout or ""):
        return "timeout"
    if run.exit_code == 137:
        return "oom"
    t = run.transcript
    if not t.events and t.tokens_total == 0:
        return "never_ran"
    if t.assistant_turns > 0 and t.tokens_total > 0:
        return "agent_error"
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

# Axes de QUALITE : ce que `combined` resume. `efficiency` en est volontairement exclu.
# Melanger qualite et cout dans un seul chiffre revient a repondre "cette config est-elle
# meilleure PAR TOKEN ?" alors que la question du benchmark est "cette config est-elle
# meilleure ?" - et comme une config riche coute mecaniquement plus cher (x3 en tokens sur un
# run reel), l'axe cout annulait a lui seul le gain de qualite (delta -0.03 avec, +0.06 sans).
# Le cout reste mesure (axe `efficiency`, tableau de cout du rapport, metriques MLflow), il est
# juste lu a cote de la qualite plutot que moyenne avec elle.
QUALITY_AXES = ["functional", "platform", "repro", "safety"]


def skipped(name: str, detail: str) -> Check:
    """Check neutre : n'entre dans aucun axe (ex. verification d'absence sur un workspace vide,
    binaire de rendu absent). Evite qu'une cellule ou l'agent n'a rien produit obtienne
    safety=1.0 'par defaut'."""
    return Check(name, True, 0.0, axis="skipped", detail=detail)


def combined_score(axis_scores: dict[str, float | None]) -> float | None:
    """Moyenne non ponderee des axes de QUALITE effectivement mesures (cf. QUALITY_AXES).
    `efficiency` n'entre pas dans ce score - voir le commentaire de QUALITY_AXES."""
    vals = [v for k, v in axis_scores.items() if v is not None and k in QUALITY_AXES]
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
