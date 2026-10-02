"""Helpers Kubernetes pour l'isolation par pod (`--isolation pod`).

Tout passe par les binaires `kubectl`/`tar` en sous-processus (pas de dependance
`kubernetes` en plus - meme logique que `opencode_driver.py` qui wrappe le CLI `opencode`).

Le Job ne fait tourner `opencode` nulle part comme PID 1 (son conteneur ne fait que
`sleep`) : l'agent est lance via un `kubectl exec` separe, dont on capture directement
stdout/stderr/code de sortie. `kubectl logs`/le statut du Job ne refletent donc jamais le
resultat de l'agent - ne pas s'y fier pour le grading.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from bench.cellenv import GIT_IDENTITY_ENV

# Image Onyxia de BASE des cellules. Choix, verifie sur la chaine d'images
# InseeFrLab/images-datascience :
#   * `r-python-julia` : R + Python (+ uv, quarto, duckdb, git, curl, tar, kubectl...) ; t05/t26
#     demandent du R, une image Python seule handicaperait l'agent sur ces taches ;
#   * variante `vscode` : c'est elle qui embarque `opencode` (install-opencode.sh), requis pour
#     le bras OpenCode ; aucune image Onyxia n'embarque node/npm ;
#   * tag DATE, pas le flottant `r4.6.1-py3.13.15` : fige R, Python et la version d'opencode
#     cuite dans l'image, donc des runs comparables dans le temps ;
#   * Python 3.13 (celui de l'environnement de dev) plutot que 3.14 tout juste sorti.
# amd64 uniquement, ~2,8 Gio. C'est le `ARG BASE_IMAGE` de docker/pod/Dockerfile.
UPSTREAM_POD_IMAGE = "inseefrlab/onyxia-vscode-r-python-julia:r4.6.1-py3.13.15-2026.09.07"

# Image par defaut des cellules (`--isolation pod`, `--pod-image`) : la base ci-dessus + Claude Code
# + les bibliotheques des taches, construite et publiee par .github/workflows/pod-image.yml.
# Tag = <tag de la base>-claude<version> : epingle, donc comparable dans le temps. Le paquet GHCR
# doit etre PUBLIC (les Jobs n'ont pas d'imagePullSecrets). tests/test_pod_image_files.py echoue si
# ce tag diverge du Dockerfile (ARG BASE_IMAGE / CLAUDE_VERSION) ou du workflow (nom de l'image).
# Pour repartir de l'image amont seule : `--pod-image <UPSTREAM_POD_IMAGE>` (claude y est alors
# installe au demarrage du pod, ~1 min 45 s par cellule).
DEFAULT_POD_IMAGE = "ghcr.io/micedre/onyxia-agent-bench-pod:r4.6.1-py3.13.15-2026.09.07-claude2.1.286"

LABEL_APP = "app"
APP_VALUE = "onyxia-agent-bench"
LABEL_RUN_ID = "bench/run-id"


def sanitize_label(s: str) -> str:
    """Rend `s` valide comme valeur de label Kubernetes (DNS-1123, <=63 car.)."""
    s = re.sub(r"[^A-Za-z0-9_.-]", "-", str(s)).strip("-_.")
    return (s or "x")[:63]


def _run(cmd: list[str], *, input_bytes: bytes | None = None, text: bool = True,
        timeout: float | None = None, check: bool = True):
    r = subprocess.run(cmd, input=input_bytes, capture_output=True, text=text,
                       timeout=timeout)
    if check and r.returncode != 0:
        err = r.stderr if isinstance(r.stderr, str) else (r.stderr or b"").decode(
            "utf-8", errors="replace")
        raise RuntimeError(f"`{' '.join(cmd)}` a echoue (code {r.returncode}) : {err.strip()}")
    return r


def kubectl_apply(manifest: dict, *, timeout: float = 30):
    """Cree/actualise un objet via `kubectl apply -f -`, manifeste passe par stdin
    (jamais en argument de commande - important pour le Secret, cf. build_secret_manifest)."""
    _run(["kubectl", "apply", "-f", "-"],
        input_bytes=json.dumps(manifest).encode("utf-8"), text=False, timeout=timeout)


def delete(kind: str, name: str, namespace: str, *, timeout: float = 30, wait: bool = False):
    """Supprime un objet. `wait=True` attend la disparition effective (utile en fin de cellule :
    sinon le pod en `Terminating` chevauche le pod de la cellule suivante et le nombre reel de
    pods concurrents depasse --workers, ce qui favorise les echecs d'ordonnancement/quota)."""
    args = ["kubectl", "delete", kind, name, "-n", namespace, "--ignore-not-found=true"]
    if wait:
        args += ["--wait=true", f"--timeout={int(timeout)}s"]
    else:
        args += ["--wait=false"]
    try:
        subprocess.run(args, capture_output=True, timeout=timeout + 15)
    except subprocess.TimeoutExpired:
        pass  # le TTL/activeDeadline cote cluster prend le relais


def build_job_manifest(*, name: str, namespace: str, image: str, secret_name: str,
                       run_id: str, task_id: str, config_id: str, seed: int, model: str,
                       sleep_seconds: int, active_deadline_s: int, ttl_after_finished_s: int,
                       resources: dict) -> dict:
    labels = {
        LABEL_APP: APP_VALUE,
        LABEL_RUN_ID: sanitize_label(run_id),
        "bench/task": sanitize_label(task_id),
        "bench/config": sanitize_label(config_id),
        "bench/seed": sanitize_label(seed),
        "bench/model": sanitize_label(model),
    }
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name, "namespace": namespace, "labels": labels,
            "annotations": {"bench/model-raw": model},
        },
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": active_deadline_s,
            "ttlSecondsAfterFinished": ttl_after_finished_s,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [{
                        "name": "cell",
                        "image": image,
                        "command": ["sleep", str(sleep_seconds)],
                        "envFrom": [{"secretRef": {"name": secret_name}}],
                        # identite git commune (cf. bench/cellenv.py) : heritee par chaque exec
                        "env": [{"name": k, "value": v} for k, v in GIT_IDENTITY_ENV.items()],
                        "resources": resources,
                    }],
                },
            },
        },
    }


def build_secret_manifest(*, name: str, namespace: str, run_id: str,
                          string_data: dict[str, str]) -> dict:
    """Secret Opaque `string_data` (cle -> valeur), reference par chaque Job via `envFrom` :
    chaque cle devient une variable d'environnement du conteneur, donc de tout `kubectl exec`.
    Specifique a l'agent : OPENCODE_ONYXIA_* pour opencode, CLAUDE_CODE_OAUTH_TOKEN pour claude."""
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": name, "namespace": namespace,
            "labels": {LABEL_APP: APP_VALUE, LABEL_RUN_ID: sanitize_label(run_id)},
        },
        "type": "Opaque",
        "stringData": dict(string_data),
    }


def _pod_names(job_name: str, namespace: str) -> list[str]:
    r = subprocess.run(["kubectl", "get", "pod", "-l", f"job-name={job_name}", "-n", namespace,
                        "-o", "jsonpath={.items[*].metadata.name}"],
                       capture_output=True, text=True, timeout=15)
    return r.stdout.split() if r.returncode == 0 else []


def wait_pod_exists(job_name: str, namespace: str, timeout_s: float) -> str:
    """Attend que le controleur Job ait cree le pod. `kubectl apply` rend la main des que le
    Job est persiste, avant la creation du pod ; `kubectl wait` sur un selecteur sans objet
    echoue immediatement ("no matching resources found") - constate sur de vrais runs."""
    deadline = time.time() + timeout_s
    while True:
        names = _pod_names(job_name, namespace)
        if names:
            return names[0]
        if time.time() >= deadline:
            raise RuntimeError(f"aucun pod cree pour le job {job_name} apres {timeout_s:.0f}s "
                               "(quota ResourceQuota ? controleur Job en retard ?)")
        time.sleep(2)


def wait_pod_ready(job_name: str, namespace: str, timeout_s: int) -> str:
    """Attend que le pod du Job existe puis soit Ready, renvoie son nom."""
    t0 = time.time()
    pod_name = wait_pod_exists(job_name, namespace, min(60, timeout_s))
    remaining = max(5, int(timeout_s - (time.time() - t0)))
    _run(["kubectl", "wait", "--for=condition=Ready", "pod", pod_name,
         "-n", namespace, f"--timeout={remaining}s"], timeout=remaining + 15)
    return pod_name


def diagnose_job(job_name: str, namespace: str, *, timeout: float = 20) -> str:
    """Capture ce qu'il faut pour comprendre un pod jamais pret (quota, image, noeud...) :
    a appeler AVANT la suppression du Job, qui detruit ces informations."""
    chunks = []
    cmds = [
        ["kubectl", "get", "job", job_name, "-n", namespace, "-o", "yaml"],
        ["kubectl", "get", "pod", "-l", f"job-name={job_name}", "-n", namespace, "-o", "wide"],
    ]
    for pod in _pod_names(job_name, namespace)[:1]:
        cmds.append(["kubectl", "describe", "pod", pod, "-n", namespace])
    cmds.append(["kubectl", "get", "events", "-n", namespace, "--sort-by=.lastTimestamp",
                 "--field-selector", f"involvedObject.name={job_name}"])
    cmds.append(["kubectl", "get", "resourcequota", "-n", namespace, "-o", "wide"])
    for cmd in cmds:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            chunks.append(f"$ {' '.join(cmd)}\n{r.stdout}{r.stderr}")
        except (subprocess.TimeoutExpired, OSError) as e:
            chunks.append(f"$ {' '.join(cmd)}\n[erreur: {e}]")
    return "\n\n".join(chunks)


def check_binary(pod: str, namespace: str, binary: str, *, timeout: float = 15) -> bool:
    r = subprocess.run(["kubectl", "exec", pod, "-n", namespace, "--",
                        "sh", "-c", f"command -v {binary}"],
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode == 0


def push_workspace(ws: Path, pod: str, namespace: str, workdir: str, *, timeout: float):
    """tar local -> pipe -> tar dans le conteneur. Preserve les modes de fichier
    (contrairement a `kubectl cp`, cf. son propre --help)."""
    tarred = subprocess.run(["tar", "-C", str(ws), "-czf", "-", "."],
                            capture_output=True, timeout=timeout)
    if tarred.returncode != 0:
        raise RuntimeError(f"tar local a echoue : {tarred.stderr.decode(errors='replace')}")
    wd = shlex.quote(workdir)
    r = subprocess.run(["kubectl", "exec", "-i", pod, "-n", namespace, "--",
                        "sh", "-c", f"mkdir -p {wd} && tar -C {wd} -xzf -"],
                       input=tarred.stdout, capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"push workspace a echoue : {r.stderr.decode(errors='replace')}")


def pull_workspace(pod: str, namespace: str, workdir: str, ws: Path, *, timeout: float):
    r = subprocess.run(["kubectl", "exec", pod, "-n", namespace, "--",
                        "tar", "-C", workdir, "-czf", "-", "."],
                       capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"pull workspace a echoue : {r.stderr.decode(errors='replace')}")
    extracted = subprocess.run(["tar", "-C", str(ws), "-xzf", "-"], input=r.stdout,
                               capture_output=True, timeout=timeout)
    if extracted.returncode != 0:
        raise RuntimeError(f"tar local (extraction) a echoue : "
                          f"{extracted.stderr.decode(errors='replace')}")


def sweep_orphans(namespace: str, current_run_id: str, max_age_s: float, *,
                  timeout: float = 30):
    """Supprime les Jobs/Secrets `app=onyxia-agent-bench` d'un run different et plus vieux
    que `max_age_s` (pas un menage global - un autre run pourrait tourner en parallele
    dans le meme namespace)."""
    now = datetime.now(UTC)
    for kind in ("jobs", "secrets"):
        r = subprocess.run(["kubectl", "get", kind, "-n", namespace,
                            "-l", f"{LABEL_APP}={APP_VALUE}", "-o", "json"],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            continue
        try:
            items = json.loads(r.stdout).get("items", [])
        except json.JSONDecodeError:
            continue
        for item in items:
            meta = item.get("metadata", {})
            labels = meta.get("labels", {})
            if labels.get(LABEL_RUN_ID) == sanitize_label(current_run_id):
                continue
            created_raw = meta.get("creationTimestamp")
            if not created_raw:
                continue
            created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            if (now - created).total_seconds() > max_age_s:
                delete(kind[:-1], meta["name"], namespace, timeout=timeout)
