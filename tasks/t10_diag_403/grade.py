"""Notation de T10 : on juge le TEXTE ASSISTANT (bonne cause, rapidite en tours LLM, pas de
fausse piste dans la conclusion). Les sorties d'outils ne comptent pas : la bonne reponse
figure litteralement dans les skills/AGENTS.md que l'agent peut lire."""
import re

from bench.grading import Check, first_match_turn

CAUSE = [r"jeton.{0,40}expir", r"token.{0,40}expir", r"expir\w*.{0,60}(jeton|token|identifiant|credential)",
         r"7\s*jours", r"sept\s+jours", r"une\s+semaine", r"renouvel", r"r[ée]g[ée]n[ée]r",
         r"reouvr\w*|rouvr\w*|relanc\w*\s+(le\s+|ton\s+|votre\s+)?service", r"credentials?.{0,40}expir",
         r"nouveaux?\s+(jeton|identifiant|token)", r"session\s+(s3\s+)?expir"]
WRONG = [r"\biam\b", r"bucket\s*policy|politique\s+de\s+bucket", r"pare-feu|firewall",
         r"probl[èe]me\s+r[ée]seau|network\s+(issue|problem)|panne\s+r[ée]seau"]


def grade(ctx):
    checks = []
    t = ctx.transcript
    turn = first_match_turn(t, CAUSE)
    correct = turn is not None
    checks.append(Check("correct_root_cause", passed=correct,
                        score=1.0 if correct else 0.0, axis="functional", weight=2.0,
                        detail=f"cause enoncee au tour {turn}" if correct else
                        f"cause non enoncee ({len(t.message_events)} message(s) assistant)"))
    # rapidite : en tours LLM (un tour = un appel modele), pas en evenements
    if turn is None:
        s = 0.0
    elif turn <= 2:
        s = 1.0
    elif turn <= 4:
        s = 0.6
    else:
        s = 0.3
    ctx.metrics["steps_to_diagnosis"] = turn if turn is not None else -1
    checks.append(Check("fast_diagnosis", passed=s >= 0.6, score=s, axis="functional",
                        detail=f"tours avant diagnostic={turn}"))
    # la conclusion (dernier message) ne part pas sur une fausse piste
    final = t.message_events[-1].text if t.message_events else ""
    wrong = [p for p in WRONG if re.search(p, final, re.IGNORECASE)]
    # une mention pour l'ecarter ("ce n'est pas un probleme IAM") est acceptee
    negated = bool(re.search(r"(?i)(pas|ni|non|not|aucun)\b.{0,40}(iam|r[ée]seau|network|firewall|pare-feu|policy)", final))
    ok = not wrong or negated
    checks.append(Check("no_wrong_cause_in_conclusion", ok, 1.0 if ok else 0.0, weight=0.5,
                        detail="ok" if ok else f"fausses pistes dans la conclusion: {wrong}"))
    return checks
