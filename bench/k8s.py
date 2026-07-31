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
from datetime import datetime, timezone
from pathlib import Path

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


def delete(kind: str, name: str, namespace: str, *, timeout: float = 30):
    subprocess.run(["kubectl", "delete", kind, name, "-n", namespace,
                    "--wait=false", "--ignore-not-found=true"],
                   capture_output=True, timeout=timeout)


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
                        "resources": resources,
                    }],
                },
            },
        },
    }


def build_secret_manifest(*, name: str, namespace: str, run_id: str,
                          base_url: str, api_key: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": name, "namespace": namespace,
            "labels": {LABEL_APP: APP_VALUE, LABEL_RUN_ID: sanitize_label(run_id)},
        },
        "type": "Opaque",
        "stringData": {
            "OPENCODE_ONYXIA_BASE_URL": base_url,
            "OPENCODE_ONYXIA_API_KEY": api_key,
        },
    }


def wait_pod_ready(job_name: str, namespace: str, timeout_s: int) -> str:
    """Attend que le pod du Job soit Ready, renvoie son nom."""
    _run(["kubectl", "wait", "--for=condition=Ready", "pod",
         "-l", f"job-name={job_name}", "-n", namespace, f"--timeout={timeout_s}s"],
        timeout=timeout_s + 15)
    r = _run(["kubectl", "get", "pod", "-l", f"job-name={job_name}", "-n", namespace,
             "-o", "jsonpath={.items[0].metadata.name}"], timeout=15)
    pod_name = r.stdout.strip()
    if not pod_name:
        raise RuntimeError(f"pod introuvable pour job {job_name}")
    return pod_name


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
    now = datetime.now(timezone.utc)
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
