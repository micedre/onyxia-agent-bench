"""Les sorties declarees se jugent sur le DISQUE, pas sur la visibilite git.

Cas reel (t18) : tous les agents mettent le CSV genere dans `.gitignore` (la regle « pas de donnees
dans Git » de la plateforme). Le grader ne voyait que les fichiers visibles par git, donc jugeait
« absente » une sortie correctement regeneree, et plafonnait la tache pres de 0.47. La suppression
avant re-execution doit elle aussi se faire sur le disque : sinon le CSV de l'agent resterait dans la
copie et la verification passerait a vide."""
from pathlib import Path

from bench import outcome
from bench.grading import deliverable_files

WRITER = "open('out.csv', 'w').write('k,v\\nA,1\\n')\n"
NOOP = "print('je ne regenere rien')\n"


def test_gitignored_output_is_found_on_disk_but_not_as_a_deliverable(make_ws):
    ws = make_ws({"run.py": WRITER, ".gitignore": "out.csv\n", "out.csv": "k,v\nA,1\n"})
    assert outcome.find(ws, "out.csv") is None                     # git ne le voit pas
    assert deliverable_files(ws, ["out.csv"]) == []
    found = outcome.find_output(ws, "out.csv")
    assert found is not None and found.name == "out.csv"          # le disque, si


def test_regenerated_gitignored_output_passes(make_ws):
    """Le cas t18 : script correct, sortie dans .gitignore."""
    ws = make_ws({"run.py": WRITER, ".gitignore": "out.csv\n", "out.csv": "k,v\nA,1\n"})
    chk, info = outcome.reexecute(ws, ["out.csv"], timeout=120)
    assert chk.passed, chk.detail
    assert info["ws"] != ws and outcome.find_output(info["ws"], "out.csv") is not None


def test_a_script_that_does_not_regenerate_still_fails_even_if_the_output_is_gitignored(make_ws):
    """Temoin negatif, decisif : sans suppression sur disque, le CSV ignore de l'agent resterait
    dans la copie et ce test passerait a vide."""
    ws = make_ws({"run.py": NOOP, ".gitignore": "out.csv\n", "out.csv": "k,v\nA,1\n"})
    chk, info = outcome.reexecute(ws, ["out.csv"], timeout=120)
    assert not chk.passed and "livrables manquants" in chk.detail and "out.csv" in chk.detail
    assert (ws / "out.csv").is_file()                              # le workspace note est intact
    assert info["ws"] == ws                                        # repli sur le livrable de l'agent


def test_visible_output_behaviour_is_unchanged(make_ws):
    ws = make_ws({"run.py": WRITER, "out.csv": "k,v\nA,1\n"})
    assert outcome.reexecute(ws, ["out.csv"], timeout=120)[0].passed
    ws2 = make_ws({"run.py": NOOP, "out.csv": "k,v\nA,1\n"})
    assert not outcome.reexecute(ws2, ["out.csv"], timeout=120)[0].passed


def test_layer_files_and_tool_caches_are_never_outputs(make_ws):
    skill = ".opencode/skills/x/assets/out.csv"
    ws = make_ws({".venv/lib/out.csv": "x", "__pycache__/out.csv": "x", ".pytest_cache/out.csv": "x"},
                 layer={skill: "k,v\nA,1\n"})
    assert outcome.find_outputs(ws, "out.csv") == []
    ws2 = make_ws({"out.csv": "k,v\nA,1\n"}, layer={skill: "k,v\nZ,9\n"})
    assert [p.name for p in outcome.find_outputs(ws2, "out.csv")] == ["out.csv"]
    assert outcome.find_output(ws2, "out.csv").parent == ws2.resolve()


def test_outputs_are_ordered_root_first(make_ws):
    ws = make_ws({"sub/out.csv": "b", "out.csv": "a"})
    assert [str(p.relative_to(ws.resolve())) for p in outcome.find_outputs(ws, "out.csv")] == ["out.csv", "sub/out.csv"]


def test_table_check_reads_a_gitignored_output(make_ws):
    ws = make_ws({".gitignore": "part.csv\n", "part.csv": "dep,part\n75,0.0\n13,0.5\n"})
    present, values = outcome.table_check(ws, "part.csv", {"75": 0.0, "13": 0.5}, name="shares_correct",
                                          key_patterns=[r"dep"], val_patterns=[r"part"], weight=2.0)
    assert present.passed and values.passed and values.score == 1.0


def test_t18_grader_now_credits_a_gitignored_regenerated_output(make_ws):
    """Bout en bout sur le vrai grader de t18 : mode d'echec observe sur les cellules Opus."""
    import json
    import shutil

    from bench.registry import discover_tasks
    from bench.runner import GradeContext
    from bench.schema import RunResult, Transcript
    repo = Path(__file__).resolve().parent.parent
    task = discover_tasks(repo / "tasks")["t18_notebook_refactor"]
    truth = json.loads((task.dir / "ground_truth.json").read_text(encoding="utf-8"))
    rows = "departement,part_communes_sous_seuil\n" + "\n".join(
        f"{d},{v}" for d, v in truth["share_below_by_dep"].items()) + "\n"
    script = ("import shutil\nshutil.copy('resultat_reference.txt', 'part_communes_sous_seuil.csv')\n")
    ws = make_ws({"bas_revenus.py": script, "resultat_reference.txt": rows, ".gitignore": "part_communes_sous_seuil.csv\n",
                  "part_communes_sous_seuil.csv": rows})
    shutil.copy2(repo / "tasks/t18_notebook_refactor/fixtures/donnees_communes.csv", ws / "donnees_communes.csv")
    t = Transcript()
    ctx = GradeContext(ws, t, [], RunResult("t18_notebook_refactor", "C0", "m", 0, ws, t), task=task)
    by = {c.name: c for c in task.grade_fn(ctx)}
    assert by["script_reexecutes"].passed, by["script_reexecutes"].detail
    assert by["shares_correct_present"].passed and by["shares_correct"].passed, by["shares_correct"].detail
