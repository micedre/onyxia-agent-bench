"""Environnement commun a toutes les cellules, quel que soit l'agent et l'isolation.

Identite git : un service Onyxia a `user.name`/`user.email` configures au demarrage (par l'init de
l'image) ; un Job de cellule, lui, n'en a aucune. Une cellule qui tente de commiter (t09, t18 et les
taches candidates) butait alors sur « unable to auto-detect email address ». Mesure sur de vrais runs :
Opus 7/7 cellules sur t09 et 5/6 sur t18, le 27B 7/10 et 6/10. Opus excluait bien le `.env`, ne stageait
que `analyse.md`, puis refusait d'inventer une identite : le grader notait « pas de commit ». Ces
taches mesuraient donc en partie « l'agent invente-t-il une identite git ? » (et si le contexte le lui
dit), pas la connaissance de la plateforme qu'elles visent.

On fournit donc la meme identite que celle du commit des fixtures (`runner._init_workspace`), en
variables d'environnement : elles priment sur toute configuration git, de sorte que le comportement
ne depend ni de l'image ni de la machine hote. Les runs anterieurs a cette correction ne sont pas
comparables sur ces taches (`meta.git_identity` les distingue).
"""
from __future__ import annotations

GIT_IDENTITY_NAME = "bench"
GIT_IDENTITY_EMAIL = "bench@local"

GIT_IDENTITY_ENV = {
    "GIT_AUTHOR_NAME": GIT_IDENTITY_NAME,
    "GIT_AUTHOR_EMAIL": GIT_IDENTITY_EMAIL,
    "GIT_COMMITTER_NAME": GIT_IDENTITY_NAME,
    "GIT_COMMITTER_EMAIL": GIT_IDENTITY_EMAIL,
}

#: Valeur de `meta.git_identity` : distingue les runs faits avec cette identite fournie.
GIT_IDENTITY_META = f"{GIT_IDENTITY_NAME} <{GIT_IDENTITY_EMAIL}> (env)"
