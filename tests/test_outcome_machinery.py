"""Invariants de `bench/outcome.py` : la notation par resultat ne doit ni detruire le
workspace qu'elle note, ni noter un fichier depose par une couche de config, ni dependre de
l'ordre du systeme de fichiers ou de la forme du chemin qu'on lui passe."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench import outcome
from bench.grading import deliverable_files

REPO = Path(__file__).resolve().parent.parent
SKILL_QMD = ".opencode/skills/quarto-publication/assets/report-template.qmd"


def test_find_ignores_layer_files(make_ws):
    """Le gabarit de la skill quarto contient `params:` : un `rglob` brut le renvoyait, ce qui
    faisait passer un check de parametrage en C2/C3/C4 alors que l'agent n'avait rien produit
    - un biais correle a la config mesuree."""
    template = (REPO / "configs/layers/skills/root" / SKILL_QMD).read_text(encoding="utf-8")
    assert "params:" in template, "fixture du test obsolete : le gabarit n'a plus de params:"
    ws = make_ws({}, layer={SKILL_QMD: template})
    assert outcome.find(ws, "*.qmd") is None
    assert deliverable_files(ws, ["*.qmd"]) == []
    # un .qmd reellement produit par l'agent est bien vu
    ws2 = make_ws({"rapport.qmd": "---\nparams:\n  region: 11\n---\n"},
                  layer={SKILL_QMD: template})
    found = outcome.find(ws2, "*.qmd")
    assert found is not None and found.name == "rapport.qmd"


def test_reexecute_does_not_touch_the_graded_workspace(make_ws):
    ws = make_ws({"run.py": "open('out.csv','w').write('k,v\\nA,1\\n')\n",
                  "out.csv": "k,v\nA,1\n"})
    before = {p.name: p.read_bytes() for p in ws.rglob("*") if p.is_file() and ".git" not in p.parts}
    chk, info = outcome.reexecute(ws, ["out.csv"], timeout=120)
    assert chk.passed, chk.detail
    assert info["ws"] != ws, "les sorties doivent etre relues dans la copie"
    after = {p.name: p.read_bytes() for p in ws.rglob("*") if p.is_file() and ".git" not in p.parts}
    assert after == before, "la notation a modifie le workspace note"


def test_reexecute_is_idempotent_and_keeps_deliverable_on_failure(make_ws):
    """Un script qui echoue ne doit pas faire disparaitre le livrable de l'agent : sinon une
    seconde notation de la meme cellule note 0 la ou la premiere notait le contenu livre."""
    ws = make_ws({"run.py": "raise SystemExit(1)\n", "out.csv": "k,v\nA,1\n"})
    for _ in range(2):
        chk, info = outcome.reexecute(ws, ["out.csv"], timeout=120)
        assert not chk.passed
        assert info["ws"] == ws, "repli : on relit le livrable de l'agent"
        assert (ws / "out.csv").is_file(), "le livrable de l'agent a ete detruit"
        assert outcome.find(info["ws"], "out.csv") is not None


def test_reexecute_accepts_several_entry_points(make_ws):
    """Un agent a le droit de separer la validation et l'agregation en deux scripts."""
    ws = make_ws({"valider.py": "open('report.json','w').write('{\"n\": 1}')\n",
                  "agreger.py": "open('agg.csv','w').write('k,v\\nA,1\\n')\n"})
    chk, info = outcome.reexecute(ws, ["report.json", "agg.csv"], timeout=120, max_candidates=3)
    assert chk.passed, chk.detail
    assert outcome.find(info["ws"], "agg.csv") is not None


def test_reexecute_never_runs_an_excluded_fixture(make_ws):
    """Une fixture executable (serveur de test) lancee comme candidat bloquerait jusqu'au
    timeout : `exclude_names` doit l'ecarter."""
    ws = make_ws({"serveur.py": "import time; time.sleep(600)\n",
                  "zzz_travail.py": "open('out.csv','w').write('k,v\\nA,1\\n')\n"})
    chk, _ = outcome.reexecute(ws, ["out.csv"], timeout=30,
                              exclude_names=("serveur.py",), max_candidates=2)
    assert chk.passed, chk.detail


def test_reexecute_uses_the_agent_project_env(make_ws):
    """Le script doit tourner dans l'environnement du PROJET (uv), pas dans celui du harnais :
    un agent qui declare sa dependance doit pouvoir etre note."""
    ws = make_ws({"pyproject.toml": "[project]\nname='x'\nversion='0'\n"
                                    "requires-python='>=3.11'\ndependencies=['tabulate']\n",
                  "run.py": "import tabulate\nopen('out.csv','w').write('k,v\\nA,1\\n')\n"})
    chk, _ = outcome.reexecute(ws, ["out.csv"], timeout=300)
    assert chk.passed, chk.detail


def test_reexecute_works_with_a_relative_workspace_path(make_ws, monkeypatch):
    """`bench regrade runs/<run>` passe un chemin relatif ; un sous-processus lance avec
    cwd=ws resolvait alors le script hors du cwd et TOUTE la notation d'execution tombait a 0."""
    ws = make_ws({"run.py": "open('out.csv','w').write('k,v\\nA,1\\n')\n"})
    monkeypatch.chdir(ws.parent.parent)
    rel = ws.relative_to(ws.parent.parent)
    chk, _ = outcome.reexecute(rel, ["out.csv"], timeout=120)
    assert chk.passed, chk.detail


def test_env_extra_reaches_the_child_without_touching_os_environ(make_ws):
    import os
    ws = make_ws({"run.py": "import os, json\n"
                            "json.dump({'v': os.environ.get('T_VAR')}, open('out.json','w'))\n"})
    chk, info = outcome.reexecute(ws, ["out.json"], timeout=120, env={"T_VAR": "42"})
    assert chk.passed, chk.detail
    assert json.loads(outcome.find(info["ws"], "out.json").read_text())["v"] == "42"
    assert "T_VAR" not in os.environ


@pytest.mark.parametrize("truth,got,tol,expected", [
    ({"01": 100.0}, {"1": 100.0}, 1.0, 1.0),      # code sans zero en tete accepte via alt_key
    ({"01": 100.0}, {"01": 100.9}, 1.0, 1.0),
    ({"01": 100.0}, {"01": 102.0}, 1.0, 0.0),
])
def test_compare_keyed_absolute_tolerance(truth, got, tol, expected):
    c = outcome.compare_keyed(got, truth, name="x", rel_tol=0.0, abs_tol=tol,
                              alt_key=lambda k: k.lstrip("0"))
    assert c.score == expected
