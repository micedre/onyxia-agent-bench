"""Fichiers de l'image des cellules (docker/pod/) : coherents entre eux et avec les defauts du
harnais. Aucun Docker ici : la construction reelle se fait dans .github/workflows/pod-image.yml."""
import os
import re
import subprocess
from pathlib import Path

import yaml

from bench import k8s
from bench.claude_driver import DEFAULT_POD_CLAUDE_VERSION

REPO = Path(__file__).resolve().parent.parent
POD = REPO / "docker" / "pod"
DOCKERFILE = (POD / "Dockerfile").read_text(encoding="utf-8")
WORKFLOW = yaml.safe_load((REPO / ".github/workflows/pod-image.yml").read_text(encoding="utf-8"))


def _arg(name: str) -> str:
    m = re.search(rf"^ARG {name}=(.+)$", DOCKERFILE, re.MULTILINE)
    assert m, f"ARG {name} absent du Dockerfile"
    return m.group(1).strip()


def test_dockerfile_defaults_match_the_bench_defaults():
    """Image de base du Dockerfile = image amont du harnais ; version de claude = celle que le
    harnais installe en pod pour les autres images. Sinon les deux bras ne comparent plus la
    meme chose."""
    assert _arg("BASE_IMAGE") == k8s.UPSTREAM_POD_IMAGE
    assert _arg("CLAUDE_VERSION") == DEFAULT_POD_CLAUDE_VERSION


def test_default_pod_image_is_the_published_image_for_these_dockerfile_args():
    """Le defaut du harnais doit designer exactement le tag que le workflow publie pour les ARG du
    Dockerfile : <tag de la base>-claude<version> sur l'image du workflow. Sinon un run par defaut
    tirerait une image qui n'existe pas (ImagePullBackOff, cellules `never_ran`)."""
    base_tag = _arg("BASE_IMAGE").rsplit(":", 1)[1]
    expected = f"{WORKFLOW['env']['IMAGE']}:{base_tag}-claude{_arg('CLAUDE_VERSION')}"
    assert k8s.DEFAULT_POD_IMAGE == expected


def test_scripts_are_executable_valid_bash():
    for name in ("install-claude.sh", "write-manifest.sh"):
        p = POD / name
        assert os.access(p, os.X_OK), f"{name} n'est pas executable"
        assert subprocess.run(["bash", "-n", str(p)]).returncode == 0
        assert (POD / name).read_text().startswith("#!/bin/bash")
        assert f"/opt/{name}" in DOCKERFILE  # copie dans l'image et utilisee


def test_dockerfile_follows_upstream_conventions():
    assert DOCKERFILE.index("USER root") < DOCKERFILE.index("RUN ")
    assert DOCKERFILE.rstrip().endswith("USER 1000")                # on rend la main a onyxia
    assert "/opt/fix-user-permissions.sh" in DOCKERFILE and "/opt/clean.sh" in DOCKERFILE
    assert "ENV DISABLE_AUTOUPDATER=1" in DOCKERFILE
    instructions = {line.split()[0].upper() for line in DOCKERFILE.splitlines()
                    if line.strip() and not line.lstrip().startswith(("#", "-", "&&"))
                    and line[0].isalpha()}
    assert not ({"CMD", "ENTRYPOINT"} & instructions)               # CMD code-server de la base


def test_requirements_cover_what_the_tasks_assume():
    reqs = {re.split(r"[<>=!~ ]", line.strip())[0].lower()
            for line in (POD / "requirements.txt").read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")}
    # pas dans l'image de base r-python-julia (qui n'a que duckdb numpy pandas pyarrow requests s3fs)
    assert {"scikit-learn", "mlflow", "geopandas", "matplotlib", "nbclient", "ipykernel",
            "pytest"} <= reqs
    assert not ({"pandas", "numpy", "pyarrow", "duckdb"} & reqs)  # deja dans la base


def test_workflow_publishes_to_the_expected_package():
    job = WORKFLOW["jobs"]["build"]
    assert WORKFLOW["env"]["IMAGE"] == "ghcr.io/micedre/onyxia-agent-bench-pod"
    assert WORKFLOW["permissions"] == {"contents": "read", "packages": "write"}
    steps = {s.get("name"): s for s in job["steps"]}
    # on teste AVANT de pousser, et seul le dernier build pousse
    order = [s.get("name") for s in job["steps"]]
    assert order.index("Smoke test") < order.index("Build and push")
    assert steps["Build (not pushed)"]["with"]["push"] is False
    assert steps["Build and push"]["with"]["push"] is True
    trig = WORKFLOW.get("on", WORKFLOW.get(True))
    assert "docker/pod/**" in trig["push"]["paths"] and "workflow_dispatch" in trig


def test_smoke_test_checks_the_pinned_version_and_the_task_libs():
    run = next(s["run"] for s in WORKFLOW["jobs"]["build"]["steps"] if s.get("name") == "Smoke test")
    for needle in ("claude --version", "opencode --version", "sklearn", "mlflow", "geopandas",
                   "testthat", "targets", "id -u"):
        assert needle in run, needle
