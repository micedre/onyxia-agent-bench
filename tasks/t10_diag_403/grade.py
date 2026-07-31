"""Notation offline de T10 : on juge le TRANSCRIPT (bonne cause + rapidite)."""
import re
from bench.grading import Check, first_match_step

CAUSE = [r"jeton.*expir", r"token.*expir", r"expir\w*.*(jeton|token|identifiant)",
         r"7\s*jours", r"renouvel", r"reouvr\w*|rouvr\w*", r"credentials?.*expir"]
WRONG = [r"iam", r"bucket policy|politique de bucket", r"reseau|network", r"pare-feu|firewall"]

def grade(ctx):
    checks = []
    step = first_match_step(ctx.transcript, CAUSE)          # 1er pas ou la cause apparait
    correct = step is not None
    checks.append(Check("correct_root_cause", passed=correct,
                        score=1.0 if correct else 0.0, axis="functional",
                        detail=f"cause trouvee au pas {step}" if correct else "cause non trouvee"))
    # rapidite : moins de pas = mieux
    if step is None:
        s = 0.0
    elif step <= 2:
        s = 1.0
    elif step <= 4:
        s = 0.6
    else:
        s = 0.3
    ctx.metrics["steps_to_diagnosis"] = step if step is not None else -1
    checks.append(Check("fast_diagnosis", passed=s >= 0.6, score=s, axis="functional",
                        detail=f"steps_to_diagnosis={step}"))
    return checks
