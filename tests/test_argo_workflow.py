"""Workflow Argo (argo/) : le script de l'orchestrateur execute pour de vrai avec git/python
simules (garde-fous, construction des arguments, injection), et coherence des manifestes avec le
harnais. Aucun cluster ici : le test sur cluster est decrit dans argo/README.md."""
import json
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from bench import k8s
from bench.claude_driver import DEFAULT_POD_CLAUDE_VERSION

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = yaml.safe_load((REPO / "argo/benchmark-workflowtemplate.yaml").read_text(encoding="utf-8"))
RUN = yaml.safe_load((REPO / "argo/benchmark-run.yaml").read_text(encoding="utf-8"))
RBAC = list(yaml.safe_load_all((REPO / "argo/rbac.yaml").read_text(encoding="utf-8")))
PARAMS = {p["name"]: p for p in TEMPLATE["spec"]["arguments"]["parameters"]}
BENCH = next(t for t in TEMPLATE["spec"]["templates"] if t["name"] == "benchmark")
CLEANUP = next(t for t in TEMPLATE["spec"]["templates"] if t["name"] == "cleanup")
SOURCE = BENCH["script"]["source"]


def _env(template) -> dict:
    block = template.get("script") or template.get("container")
    return {e["name"]: e["value"] for e in block["env"]}


# ------------------------------------------------------------------ manifestes


def test_every_parameter_reference_is_declared():
    text = (REPO / "argo/benchmark-workflowtemplate.yaml").read_text(encoding="utf-8")
    used = set(re.findall(r"\{\{workflow\.parameters\.([a-z_]+)\}\}", text))
    assert used <= set(PARAMS), used - set(PARAMS)
    assert set(PARAMS) - used == set(), f"parametres declares mais inutilises : {set(PARAMS) - used}"


def test_resource_quantities_are_valid_before_substitution():
    """Argo valide les quantites AVANT de substituer les parametres (erreur reelle constatee :
    "quantities must match the regular expression") : aucun `{{...}}` dans `resources` ni dans un
    `sizeLimit` ; les parametres cpu/memory/scratch_size passent par podSpecPatch."""
    block = BENCH["script"]
    quantities = [*block["resources"]["requests"].values(), *block["resources"]["limits"].values(),
                  *(v["emptyDir"]["sizeLimit"] for v in BENCH["volumes"])]
    qty = re.compile(r"^([+-]?[0-9.]+)([eEinumkKMGTP]*[-+]?[0-9]*)$")      # regle de validation d'Argo
    for q in quantities:
        assert qty.match(str(q)), q
    # le patch, une fois les parametres substitues, est du JSON valide qui vise le bon conteneur
    patch = BENCH["podSpecPatch"]
    for name, value in {"cpu": "2", "memory": "6Gi", "scratch_size": "30Gi"}.items():
        patch = patch.replace("{{workflow.parameters.%s}}" % name, value)
    assert "{{" not in patch
    spec = json.loads(patch)
    c = spec["containers"][0]
    assert c["name"] == "main" and c["resources"]["requests"] == {
        "cpu": "2", "memory": "6Gi", "ephemeral-storage": "30Gi"}
    assert spec["volumes"][0] == {"name": "scratch", "emptyDir": {"sizeLimit": "30Gi"}}
    assert spec["volumes"][0]["name"] == BENCH["volumes"][0]["name"]


def test_script_never_interpolates_parameters():
    """Les parametres passent par l'environnement : `{{...}}` dans le source shell permettrait a
    une valeur de parametre d'etre interpretee comme du shell."""
    assert "{{" not in SOURCE
    env = _env(BENCH)
    for name in ("AGENT", "MODEL", "SUITE", "TASKS", "CONFIGS", "SEEDS", "WORKERS", "ISOLATION",
                 "EXPERIMENT", "RUN_NAME", "MLFLOW", "REPO_URL", "REPO_REF", "POD_IMAGE"):
        assert env[name].startswith("{{workflow.parameters."), name


def test_defaults_follow_the_harness():
    assert PARAMS["pod_image"]["value"] == k8s.DEFAULT_POD_IMAGE
    assert PARAMS["image"]["value"] == k8s.DEFAULT_POD_IMAGE
    assert PARAMS["pod_claude_version"]["value"] == DEFAULT_POD_CLAUDE_VERSION
    assert PARAMS["configs"]["value"] == "C0,C4" and PARAMS["suite"]["value"] == "context"
    assert PARAMS["agent"]["enum"] == ["opencode", "claude"]
    assert set(PARAMS["suite"]["enum"]) == {"context", "model", "candidate", "all"}


def test_example_workflow_uses_declared_parameters_and_the_sa():
    assert RUN["spec"]["workflowTemplateRef"]["name"] == TEMPLATE["metadata"]["name"]
    assert {p["name"] for p in RUN["spec"]["arguments"]["parameters"]} <= set(PARAMS)
    assert RUN["spec"]["serviceAccountName"] == TEMPLATE["spec"]["serviceAccountName"]
    assert RUN["metadata"]["generateName"]            # => `kubectl create`, pas `apply`


def test_workflow_runs_as_the_argo_executor_service_account():
    """Le service Argo d'Onyxia ne donne `workflowtaskresults` (necessaire a l'executor dans chaque
    pod de workflow) qu'a `argo-workflows`, et un admin de namespace ne peut pas l'accorder a un
    autre SA. Le workflow doit donc tourner sous ce SA, et rbac.yaml lui ajouter NOS droits."""
    assert TEMPLATE["spec"]["serviceAccountName"] == "argo-workflows"
    assert not any(d["kind"] == "ServiceAccount" for d in RBAC)    # on n'en cree pas


def test_rbac_covers_what_the_harness_calls_and_binds_the_template_sa():
    role = next(d for d in RBAC if d["kind"] == "Role")
    binding = next(d for d in RBAC if d["kind"] == "RoleBinding")
    assert binding["subjects"][0]["kind"] == "ServiceAccount"
    assert binding["subjects"][0]["name"] == TEMPLATE["spec"]["serviceAccountName"]
    assert binding["roleRef"]["name"] == role["metadata"]["name"]
    assert binding["subjects"][0]["namespace"] == "__NAMESPACE__"   # remplace par sed, cf. l'en-tete
    can = {(g, r): set(rule["verbs"]) for rule in role["rules"]
           for g in rule["apiGroups"] for r in rule["resources"]}
    assert {"create", "get", "list", "watch", "delete"} <= can[("batch", "jobs")]
    assert {"get", "list", "watch", "delete"} <= can[("", "pods")]
    assert "create" in can[("", "pods/exec")]
    assert {"create", "get", "list", "delete"} <= can[("", "secrets")]
    assert {"get", "list"} <= can[("", "events")] and {"get", "list"} <= can[("", "resourcequotas")]
    # absent a dessein : un admin de namespace ne peut pas accorder ce droit (escalade refusee)
    assert ("argoproj.io", "workflowtaskresults") not in can
    # rien de plus large que necessaire
    assert not any("*" in rule["verbs"] or "*" in rule["resources"] for rule in role["rules"])


def test_cleanup_targets_only_this_runs_resources():
    cmd = CLEANUP["container"]["args"][0]
    assert "app=onyxia-agent-bench,bench/run-id=$label" in cmd
    assert "jobs,secrets" in cmd and "delete" in cmd
    assert TEMPLATE["spec"]["onExit"] == "cleanup"


# ------------------------------------------------------------------ script execute


FAKE_PYTHON = r"""#!/bin/bash
# `python -m bench ...` : trace les arguments (un par ligne) et ecrit un run factice.
# `python -` (heredoc)  : le pre-controle MLflow est simule ; le bilan est le vrai code.
if [ "${1:-}" = "-m" ]; then
  { echo "--- bench"; printf '%s\n' "${@:3}"; } >> "$TRACE"
  if [ -n "${FAKE_PREFLIGHT_FAIL:-}" ]; then    # pre-controle refuse : rien n'est ecrit, sortie 1
    echo "[preflight] ECHEC : claude refuse la requete (modele opus-5.5) : model not found"
    echo "pre-controle echoue pour --agent claude --model opus-5.5 (--no-preflight pour passer outre)" >&2
    exit 1
  fi
  out="$ROOT/runs/$RUN_NAME"; mkdir -p "$out"
  echo "# report factice" > "$out/report.md"
  aborted='null'
  [ -n "${FAKE_ABORTED:-}" ] && aborted='{"reason": "10 cellules perdues d affilee sans aucun token", "last_message": "Failed to authenticate 401", "cells_skipped": 40}'
  echo "{\"n_cells\": 10, \"n_valid\": ${FAKE_N_VALID:-10}, \"compared\": {\"low\": \"C0\", \"high\": \"C4\"}, \"delta_combined_paired\": {\"mean\": 0.3, \"ci95\": [0.1, 0.5], \"n\": 5}, \"meta\": {\"aborted\": $aborted}}" > "$out/summary.json"
  [ -n "${FAKE_ABORTED:-}" ] && exit 1           # comme `bench run` reel : sortie non nulle
  exit 0
fi
src=$(cat)
case "$src" in
  *set_tracking_uri*) echo "--- mlflow-preflight uri=$MLFLOW_TRACKING_URI exp=$EXPERIMENT" >> "$TRACE"
                      exit "${FAKE_MLFLOW_RC:-0}" ;;
  *) printf '%s' "$src" | "$REAL_PYTHON" - ;;
esac
"""
FAKE_GIT = r"""#!/bin/bash
echo "--- git $*" >> "$TRACE"
if [ "$1" = "fetch" ] && [ -n "${FAKE_GIT_FETCH_FAIL:-}" ]; then exit 1; fi
if [ "$1" = "rev-parse" ]; then echo abc1234; fi
exit 0
"""

BASE_ENV = {
    "AGENT": "opencode", "MODEL": "", "SUITE": "context", "TASKS": "", "CONFIGS": "C0,C4",
    "SEEDS": "5", "WORKERS": "4", "ISOLATION": "pod", "POD_IMAGE": "ghcr.io/x/y:t",
    "POD_CLAUDE_VERSION": "2.1.286", "EXPERIMENT": "exp", "RUN_NAME": "", "MLFLOW": "true",
    "REPO_URL": "https://github.com/micedre/onyxia-agent-bench", "REPO_REF": "main",
    "SECRET_NAME": "onyxia-agent-bench", "WORKFLOW_NAME": "onyxia-agent-bench-abcde",
    "NAMESPACE": "ns", "MLFLOW_TRACKING_URI": "http://mlflow.example",
    "OPENCODE_ONYXIA_BASE_URL": "http://llm", "OPENCODE_ONYXIA_API_KEY": "k",
}


@pytest.fixture
def run_script(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("python", FAKE_PYTHON), ("git", FAKE_GIT)):
        p = bindir / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    root = tmp_path / "work"
    # le script ecrit sous /work et /tmp/result : on les redirige vers le repertoire du test
    script = SOURCE.replace("/work", str(root)).replace("/tmp/result", str(tmp_path / "result"))
    (tmp_path / "script.sh").write_text(script)

    def _run(**overrides):
        env = {**BASE_ENV, **overrides}
        env.update({"PATH": f"{bindir}:/usr/bin:/bin", "TRACE": str(tmp_path / "trace"),
                    "ROOT": str(root), "REAL_PYTHON": sys.executable, "HOME": str(tmp_path)})
        env = {k: v for k, v in env.items() if v is not None}
        r = subprocess.run(["bash", str(tmp_path / "script.sh")], env=env, capture_output=True,
                           text=True, cwd=tmp_path)
        trace = (tmp_path / "trace").read_text() if (tmp_path / "trace").exists() else ""
        bench_args = []
        if "--- bench" in trace:
            bench_args = trace.split("--- bench\n", 1)[1].split("\n--- ")[0].splitlines()
        result = (tmp_path / "result").read_text() if (tmp_path / "result").exists() else None
        return r, trace, bench_args, result, tmp_path
    return _run


def _flag(args, name):
    return args[args.index(name) + 1]


def test_happy_path_opencode(run_script):
    r, trace, args, result, tmp = run_script()
    assert r.returncode == 0, r.stderr
    assert args[0] == "run" and _flag(args, "--agent") == "opencode"
    assert _flag(args, "--suite") == "context" and _flag(args, "--configs") == "C0,C4"
    assert _flag(args, "--seeds") == "5" and _flag(args, "--workers") == "4"
    assert _flag(args, "--isolation") == "pod" and _flag(args, "--pod-image") == "ghcr.io/x/y:t"
    assert _flag(args, "--pod-namespace") == "ns"
    assert _flag(args, "--run-name") == "onyxia-agent-bench-abcde"   # defaut = nom du workflow
    assert _flag(args, "--experiment") == "exp"
    assert "--model" not in args and "--tasks" not in args and "--no-mlflow" not in args
    assert "--pod-claude-version" not in args
    assert "mlflow-preflight uri=http://mlflow.example exp=exp" in trace
    assert "git fetch -q --depth 1 origin main" in trace and "git checkout -q FETCH_HEAD" in trace
    assert "10/10 cellules valides" in result and "+0.30" in result and "MLflow" in result


def test_claude_pod_run_passes_model_and_version(run_script):
    r, trace, args, result, _ = run_script(AGENT="claude", MODEL="claude-opus-5-5",
                                           CLAUDE_CODE_OAUTH_TOKEN="t",
                                           OPENCODE_ONYXIA_BASE_URL=None, OPENCODE_ONYXIA_API_KEY=None)
    assert r.returncode == 0, r.stderr
    assert _flag(args, "--agent") == "claude" and _flag(args, "--model") == "claude-opus-5-5"
    assert _flag(args, "--pod-claude-version") == "2.1.286"


def test_tasks_override_process_isolation_and_no_mlflow(run_script):
    r, trace, args, result, _ = run_script(TASKS="t10_diag_403,t03_mlflow_train", ISOLATION="process",
                                           MLFLOW="false", MLFLOW_TRACKING_URI=None, RUN_NAME="mon-run")
    assert r.returncode == 0, r.stderr
    assert _flag(args, "--tasks") == "t10_diag_403,t03_mlflow_train"
    assert "--no-mlflow" in args and _flag(args, "--run-name") == "mon-run"
    assert "--pod-image" not in args and "--pod-namespace" not in args
    assert "mlflow-preflight" not in trace and "MLflow" not in result


@pytest.mark.parametrize("overrides, needle", [
    ({"AGENT": "gpt"}, "agent 'gpt' invalide"),
    ({"ISOLATION": "vm"}, "isolation 'vm' invalide"),
    ({"SUITE": "tout"}, "suite 'tout' invalide"),
    ({"MLFLOW": "yes"}, "mlflow 'yes' invalide"),
    ({"SEEDS": "0"}, "seeds"), ({"SEEDS": "abc"}, "seeds"), ({"WORKERS": "-1"}, "workers"),
    ({"CONFIGS": "C0;C9"}, "configs"), ({"CONFIGS": "C5"}, "configs"),
    ({"TASKS": "t1; rm -rf /"}, "tasks"), ({"TASKS": "a b"}, "tasks"),
    ({"REPO_URL": "http://insecure/x"}, "repo_url"), ({"REPO_URL": "https://h/x;id"}, "repo_url"),
    ({"REPO_REF": "main; id"}, "repo_ref"), ({"RUN_NAME": "a b"}, "run_name"),
    ({"RUN_NAME": "x/../y"}, "run_name"), ({"EXPERIMENT": ""}, "experiment vide"),
])
def test_bad_parameters_stop_before_anything_runs(run_script, overrides, needle):
    r, trace, args, result, _ = run_script(**overrides)
    assert r.returncode == 1 and needle in r.stderr, r.stderr
    assert "--- bench" not in trace and "--- git" not in trace      # rien n'a ete lance


@pytest.mark.parametrize("overrides, needle", [
    ({"AGENT": "claude", "MODEL": ""}, "model est obligatoire"),
    ({"MODEL": "opus"}, "est un modele Claude mais agent=opencode"),             # le run reel
    ({"MODEL": "claude-opus-5-5"}, "est un modele Claude mais agent=opencode"),
    ({"MODEL": "sonnet"}, "relancer avec agent=claude"),
    ({"AGENT": "claude", "MODEL": "onyxia/qwen3-8-27b", "CLAUDE_CODE_OAUTH_TOKEN": "t"},
     "forme provider/model d'un modele opencode mais agent=claude"),
    ({"AGENT": "claude", "MODEL": "m", "CLAUDE_CODE_OAUTH_TOKEN": None}, "CLAUDE_CODE_OAUTH_TOKEN absent"),
    ({"OPENCODE_ONYXIA_API_KEY": None}, "OPENCODE_ONYXIA_BASE_URL / OPENCODE_ONYXIA_API_KEY absents"),
    ({"MLFLOW_TRACKING_URI": None}, "perdus avec le pod"),
    ({"MLFLOW_TRACKING_URI": ""}, "perdus avec le pod"),
])
def test_missing_credentials_or_mlflow_stop_early(run_script, overrides, needle):
    r, trace, args, result, _ = run_script(**overrides)
    assert r.returncode == 1 and needle in r.stderr, r.stderr
    assert "--- bench" not in trace
    assert result is not None and "echec" in result      # sortie `result` lisible meme en echec precoce


def test_unreachable_mlflow_stops_before_the_long_run(run_script):
    r, trace, args, _, _ = run_script(FAKE_MLFLOW_RC="1")
    assert r.returncode == 1 and "MLflow injoignable" in r.stderr
    assert "--- bench" not in trace and "--- git" not in trace


def test_git_fetch_failure_is_readable(run_script):
    r, trace, args, _, _ = run_script(FAKE_GIT_FETCH_FAIL="1")
    assert r.returncode == 1 and "git fetch 'main'" in r.stderr and "--- bench" not in trace


def test_a_run_with_no_valid_cell_fails_the_workflow(run_script):
    """Le harnais sort en 0 meme si aucune cellule n'a tourne : le workflow doit echouer."""
    r, trace, args, result, _ = run_script(FAKE_N_VALID="0")
    assert r.returncode == 1
    assert "0/10 cellules valides" in result            # le bilan reste lisible dans l'output


def test_preflight_failure_fails_the_workflow_and_names_the_cause(run_script):
    """Le pre-controle de l'agent echoue (modele mal nomme...) : aucun resultat, workflow en echec,
    et la cause est dans le parametre de sortie, pas seulement enfouie dans les logs."""
    r, trace, args, result, _ = run_script(FAKE_PREFLIGHT_FAIL="1", AGENT="claude",
                                           MODEL="opus-5.5", CLAUDE_CODE_OAUTH_TOKEN="t")
    assert r.returncode == 1
    assert "[preflight] ECHEC" in r.stdout                       # visible dans les logs du workflow
    assert result is not None and result.startswith("[preflight] ECHEC")
    assert "opus-5.5" in result and "model not found" in result
    assert "report factice" not in r.stdout                      # pas de bilan : rien n'a tourne


def test_aborted_run_fails_the_workflow_with_the_reason(run_script):
    """Run interrompu par le disjoncteur : meme avec des cellules valides, ce n'est pas un resultat.
    Le bilan et la raison sont produits AVANT de sortir en echec."""
    r, trace, args, result, _ = run_script(FAKE_ABORTED="1")
    assert r.returncode == 1
    assert "report factice" in r.stdout                          # le rapport est quand meme affiche
    assert "RUN INTERROMPU" in result and "40 cellules non lancees" in result
    assert "Failed to authenticate 401" in result


def test_hostile_values_are_inert(run_script):
    """Une valeur de parametre n'est jamais interpretee par le shell : soit refusee par la
    validation, soit transmise intacte comme UN argument (modele, experience)."""
    evil_model = 'm"; touch PWNED; echo "'
    evil_exp = "x$(touch PWNED2) `touch PWNED3` & touch PWNED4"
    r, trace, args, _, tmp = run_script(MODEL=evil_model, EXPERIMENT=evil_exp)
    assert r.returncode == 0, r.stderr
    assert _flag(args, "--model") == evil_model and _flag(args, "--experiment") == evil_exp
    for f in ("PWNED", "PWNED2", "PWNED3", "PWNED4"):
        assert not (tmp / f).exists() and not (tmp / "work/repo" / f).exists(), f
    r2, *_ = run_script(TASKS="x; touch PWNED5")
    assert r2.returncode == 1 and not (tmp / "PWNED5").exists()
