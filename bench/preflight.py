"""Pre-controle avant un run : l'agent repond-il avec CE modele et CES identifiants ?

Un identifiant de modele faux (`opus-5.5` au lieu de `claude-opus-5-5`) ou un jeton refuse donnent
exactement la meme cellule : un seul tour, zero token, quelques secondes. Sans pre-controle on ne
l'apprend qu'apres avoir lance toute la matrice (50 cellules d'affilee dans un run reel). Ici, un
seul appel minuscule, AVANT de creer le moindre objet k8s ou de lancer une cellule, et le message
de l'agent est affiche tel quel : on ne depend pas de sa formulation.

    python -m bench preflight --agent claude --model claude-opus-5-5
    python -m bench preflight --agent opencode --model onyxia/qwen3-8-27b
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

_MSG_MAX = 400
# Alias de modele de Claude Code, et identifiants complets (claude-opus-5-5, claude-haiku-4-5-2025...).
_CLAUDE_MODEL_RE = re.compile(r"^(claude-[A-Za-z0-9._-]+|opus|sonnet|haiku|fable)(\[1m\])?$")


@dataclass
class PreflightResult:
    ok: bool
    message: str
    skipped: bool = False     # controle non realisable ici (binaire absent, endpoint sans /models)


def _one_line(text: str, limit: int = _MSG_MAX) -> str:
    return " ".join((text or "").split())[:limit]


def agent_mismatch(agent: str, model: str) -> str:
    """Modele Claude lance avec opencode (ou `provider/model` avec claude) : l'erreur la plus facile
    a faire (`agent` vaut `opencode` par defaut) et la plus opaque (OpenCode ne connait pas `opus` :
    zero tour, cellules `never_ran`). Vide si rien d'anormal."""
    if agent == "opencode" and _CLAUDE_MODEL_RE.match(model or "") and "/" not in model:
        return (f"`{model}` est un modele Claude, or l'agent est `opencode` (le defaut) : "
                "lancer avec --agent claude (parametre `agent=claude` dans le workflow Argo).")
    if agent == "claude" and "/" in (model or ""):
        return (f"`{model}` a la forme `provider/model` d'un modele OpenCode, or l'agent est "
                "`claude` : lancer avec --agent opencode, ou un identifiant Claude "
                "(claude-opus-5-5).")
    return ""


def claude_model_hint(model: str) -> str:
    """Indice quand l'identifiant ne ressemble pas a un identifiant Claude (jamais bloquant seul :
    c'est l'appel reel qui fait foi)."""
    if _CLAUDE_MODEL_RE.match(model or ""):
        return ""
    return (f" -> `{model}` ne ressemble pas a un identifiant de modele Claude : ils ont la forme "
            "`claude-opus-5-5` (ou un alias : opus, sonnet, haiku).")


def _result_event(stdout: str) -> dict | None:
    """Dernier evenement `result` du flux stream-json de `claude -p`."""
    found = None
    for line in (stdout or "").splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            found = obj
    return found


def check_claude(model: str, *, binary: str = "claude", timeout: int = 120) -> PreflightResult:
    """Un `claude -p` minimal avec les memes options qu'une cellule (config isolee, pas de MCP)."""
    from bench.claude_driver import build_command, isolated_config_dir, subprocess_env

    if shutil.which(binary) is None:
        return PreflightResult(True, f"`{binary}` absent de cette machine : pre-controle ignore "
                               "(les cellules s'executent ailleurs)", skipped=True)
    cfg_dir = isolated_config_dir()
    work = tempfile.mkdtemp(prefix="bench-preflight-")
    try:
        r = subprocess.run(build_command(binary, "Reponds uniquement par le mot OK.", model),
                           cwd=work, env=subprocess_env(cfg_dir, Path(work)), capture_output=True,
                           text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return PreflightResult(False, f"`claude` n'a pas repondu en {timeout} s (reseau ? "
                               "api.anthropic.com joignable ?)")
    finally:
        shutil.rmtree(cfg_dir, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)
    res = _result_event(r.stdout)
    if res is not None and not res.get("is_error") and r.returncode == 0:
        return PreflightResult(True, f"claude OK avec le modele `{model}`")
    if res is not None:
        text = _one_line(str(res.get("result") or ""))
    else:
        text = _one_line(r.stderr) or f"aucune reponse (code de sortie {r.returncode})"
    return PreflightResult(False, f"claude refuse la requete (modele `{model}`) : {text}"
                           + claude_model_hint(model))


def _model_ids(payload) -> list[str]:
    items = payload.get("data") if isinstance(payload, dict) else payload
    ids = []
    for it in items or []:
        if isinstance(it, dict) and it.get("id"):
            ids.append(str(it["id"]))
        elif isinstance(it, str):
            ids.append(it)
    return ids


def check_opencode(model: str, *, base_url: str | None = None, api_key: str | None = None,
                   timeout: int = 20) -> PreflightResult:
    """GET {base}/models (API OpenAI-compatible) : l'endpoint repond-il, la cle est-elle acceptee,
    le modele existe-t-il ? Seul le fournisseur `onyxia` de la config du harnais est controle."""
    provider, _, model_id = model.partition("/")
    if not model_id:
        return PreflightResult(False, f"modele `{model}` : forme attendue `provider/model` "
                               "(ex. onyxia/qwen3-8-27b)")
    if provider != "onyxia":
        return PreflightResult(True, f"fournisseur `{provider}` : pre-controle ignore", skipped=True)
    base_url = base_url or os.environ.get("OPENCODE_ONYXIA_BASE_URL")
    api_key = api_key or os.environ.get("OPENCODE_ONYXIA_API_KEY")
    if not base_url or not api_key:
        return PreflightResult(False, "OPENCODE_ONYXIA_BASE_URL / OPENCODE_ONYXIA_API_KEY manquants")
    req = urllib.request.Request(base_url.rstrip("/") + "/models",
                                 headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (URL de la config)
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return PreflightResult(True, f"{base_url}/models absent (HTTP 404) : liste des modeles "
                                   "non verifiable, pre-controle ignore", skipped=True)
        hint = " : cle refusee" if e.code in (401, 403) else ""
        return PreflightResult(False, f"{base_url}/models -> HTTP {e.code}{hint}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return PreflightResult(False, f"{base_url} injoignable : {_one_line(str(e), 200)}")
    except json.JSONDecodeError:
        return PreflightResult(True, f"{base_url}/models ne rend pas du JSON : liste des modeles "
                               "non verifiable, pre-controle ignore", skipped=True)
    ids = _model_ids(payload)
    if model_id in ids:
        return PreflightResult(True, f"endpoint OK, modele `{model_id}` disponible")
    shown = ", ".join(sorted(ids)[:10]) or "(liste vide)"
    return PreflightResult(False, f"modele `{model_id}` absent de {base_url}/models ; "
                           f"disponibles : {shown}{' ...' if len(ids) > 10 else ''}")


def run_preflight(agent: str, model: str) -> PreflightResult:
    wrong = agent_mismatch(agent, model)
    if wrong:                      # inutile d'appeler quoi que ce soit : la reponse est connue
        return PreflightResult(False, wrong)
    if agent == "claude":
        return check_claude(model)
    return check_opencode(model)
