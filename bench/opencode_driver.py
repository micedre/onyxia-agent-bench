"""Pilotage d'OpenCode.

RealOpenCodeDriver : lance `opencode run -m <provider/model> --format json "<prompt>"`
dans le workspace isole (`--dir` + `PWD` : `opencode run` resout son repertoire depuis
$PWD, pas depuis le cwd du process), capture la sortie et la transforme en Transcript.
Isolation "legere" : sous-repertoire + git sur le systeme de fichiers de l'hote, donc pas
une frontiere de securite - l'agent peut toujours sortir du workspace par chemin absolu
(voir `--isolation pod` et les limites connues du README).

PodOpenCodeDriver : meme contrat, mais l'agent tourne dans un Job/pod Kubernetes ephemere
(`--isolation pod`) - le systeme de fichiers du conteneur est la frontiere d'isolation,
donc rien d'autre que le workspace pousse n'existe pour l'agent. Voir bench/k8s.py.

MockOpenCodeDriver : simule un agent (comportement fonction de la config) pour valider
tout le harnais sans vrai modele. Actif via `--dry-run`.
"""
from __future__ import annotations

import itertools
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from bench import k8s
from bench.cellenv import GIT_IDENTITY_ENV
from bench.k8s import PULL_TIMEOUT_S
from bench.schema import PULL_FAILED_MARKER, Event, RunResult, TaskSpec, Transcript

# --------------------------------------------------------------------------------------
# Parsing de la sortie `opencode run --format json`.
#
# Format reel (opencode 1.18.x) : nd-JSON, un objet par ligne, quatre cles de premier niveau
# `type`, `timestamp`, `sessionID`, `part` - TOUT le contenu utile est sous `part` :
#   {"type":"step_start",  "part":{"type":"step-start", ...}}
#   {"type":"text",        "part":{"type":"text","text":"..."}}
#   {"type":"tool_use",    "part":{"type":"tool","tool":"bash","callID":"...",
#                                  "state":{"status":"completed|error","input":{...},
#                                           "output":"...","error":"..."}}}
#   {"type":"step_finish", "part":{"type":"step-finish","reason":"tool-calls|stop",
#                                  "tokens":{"total":..,"input":..,"output":..,"reasoning":..,
#                                            "cache":{"read":..,"write":..}},"cost":0}}
# Un `step_finish` = un tour LLM ; `tokens.input` est la taille du contexte envoye a CE tour
# (le prompt complet est renvoye a chaque tour), donc la somme sur les tours est le cout
# "facture" et la valeur du dernier tour est la taille finale du contexte.
#
# L'ancien parseur ne regardait que les cles de premier niveau (`usage`, `text`, `tool`...) :
# avec ce format il ne voyait ni tokens (toujours 0), ni texte assistant (0 evenement
# "message", donc `steps == tool_calls` et `transcript.text` retombait sur le stdout brut),
# ni nom d'outil. Constate sur 165/165 cellules d'un run reel. On garde un repli tolerant
# pour d'autres formats (objet unique, cles a plat), mais le chemin `part` est le chemin normal.
# --------------------------------------------------------------------------------------
_PERMISSION_REJECTED = "rejected permission"
_OUTPUT_KEEP = 4000  # caracteres de sortie d'outil conserves par evenement


def _as_int(x) -> int:
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        return 0


def _ingest_tokens(t: Transcript, tokens: dict):
    inp = _as_int(tokens.get("input", tokens.get("prompt_tokens")))
    out = _as_int(tokens.get("output", tokens.get("completion_tokens")))
    t.tokens_in += inp
    t.tokens_out += out
    t.tokens_reasoning += _as_int(tokens.get("reasoning"))
    cache = tokens.get("cache") or {}
    if isinstance(cache, dict):
        t.tokens_cache_read += _as_int(cache.get("read"))
        t.tokens_cache_write += _as_int(cache.get("write"))
    if inp:
        if not t.context_tokens_first:
            t.context_tokens_first = inp
        t.context_tokens_last = inp


def _ingest_part(t: Transcript, outer: dict, part: dict):
    """Un evenement au format opencode 1.18 (`part` imbrique)."""
    ptype = str(part.get("type") or outer.get("type") or "")
    turn = t.assistant_turns + 1
    if ptype in ("text", "reasoning") or outer.get("type") == "text":
        txt = part.get("text") or ""
        if txt and ptype != "reasoning":
            t.events.append(Event("message", text=str(txt), raw=outer, turn=turn))
    elif ptype == "tool" or outer.get("type") in ("tool_use", "tool", "tool_call"):
        state = part.get("state") or {}
        inp = state.get("input", part.get("input", {}))
        output = str(state.get("output") or "")
        error = str(state.get("error") or "")
        status = str(state.get("status") or "")
        name = str(part.get("tool") or part.get("name") or "tool")
        ev = Event("tool", name=name, text=json.dumps(inp, ensure_ascii=False)[:300], raw=outer,
                   status=status, output=(output or error)[:_OUTPUT_KEEP], turn=turn)
        t.events.append(ev)
        if status == "error" or error:
            t.tool_errors += 1
        if _PERMISSION_REJECTED in output or _PERMISSION_REJECTED in error:
            t.permission_rejections += 1
        if name == "task":
            t.subagent_calls += 1
    elif ptype in ("step-finish", "step_finish") or outer.get("type") == "step_finish":
        t.assistant_turns += 1
        tokens = part.get("tokens") or {}
        if isinstance(tokens, dict):
            _ingest_tokens(t, tokens)
        try:
            t.cost += float(part.get("cost") or 0)
        except (TypeError, ValueError):
            pass
    # step-start et autres : rien a extraire


def _ingest_flat(t: Transcript, obj: dict):
    """Repli tolerant pour d'autres formats (cles a plat, listes imbriquees)."""
    role = obj.get("role")
    txt = obj.get("text") or obj.get("content") or obj.get("message") or ""
    if isinstance(txt, list):  # certains formats: content = [{type,text},...]
        txt = " ".join(p.get("text", "") for p in txt if isinstance(p, dict))
    if txt and role in (None, "assistant", "model"):
        t.events.append(Event("message", text=str(txt), raw=obj))
    tname = obj.get("tool") or obj.get("tool_name") or obj.get("name")
    if obj.get("type") in ("tool", "tool_use", "tool_call") or (tname and "input" in obj):
        t.events.append(Event("tool", name=str(tname or "tool"),
                              text=json.dumps(obj.get("input", {}))[:300], raw=obj))
    usage = obj.get("usage") or obj.get("tokens") or {}
    if isinstance(usage, dict) and usage:
        t.assistant_turns += 1
        _ingest_tokens(t, usage)
    for key in ("parts", "messages", "events", "steps", "output"):
        sub = obj.get(key)
        if isinstance(sub, list):
            for s in sub:
                if isinstance(s, dict):
                    _ingest_obj(t, s)


def _ingest_obj(t: Transcript, obj: dict):
    part = obj.get("part")
    if isinstance(part, dict):
        _ingest_part(t, obj, part)
    else:
        _ingest_flat(t, obj)


def parse_output(stdout: str) -> Transcript:
    t = Transcript(raw_stdout=stdout)
    stdout = (stdout or "").strip()
    if not stdout:
        return t

    # 1) objet JSON unique / liste
    try:
        obj = json.loads(stdout)
        if isinstance(obj, dict):
            _ingest_obj(t, obj)
        elif isinstance(obj, list):
            for s in obj:
                if isinstance(s, dict):
                    _ingest_obj(t, s)
    except json.JSONDecodeError:
        # 2) nd-JSON (une ligne = un objet) ; les lignes non-JSON (warnings...) sont ignorees
        parsed_any = False
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                _ingest_obj(t, obj)
                parsed_any = True
        # 3) repli texte brut
        if not parsed_any:
            t.events.append(Event("message", text=stdout))

    # Texte assistant uniquement. Ne PAS retomber sur le stdout brut : il contient les
    # sorties d'outils (fichiers lus par l'agent), et un grader de transcript y trouverait
    # des motifs que l'agent n'a jamais enonces (constate : la cause d'un 403 lue dans un
    # troubleshooting.md comptait comme diagnostic).
    t.text = "\n".join(e.text for e in t.events if e.type == "message")
    return t


# --------------------------------------------------------------------------------------
class BaseDriver:
    name = "base"

    def run(self, task: TaskSpec, workspace: Path, model: str, seed: int,
            config_id: str) -> RunResult:
        raise NotImplementedError


class RealOpenCodeDriver(BaseDriver):
    name = "opencode"

    def __init__(self, binary: str = "opencode", extra_env: dict | None = None):
        self.binary = binary
        self.extra_env = extra_env or {}

    def run(self, task, workspace, model, seed, config_id) -> RunResult:
        env = os.environ.copy()
        env.update(GIT_IDENTITY_ENV)       # identite git commune a toutes les cellules (cellenv)
        env.update(self.extra_env)
        # opencode lit opencode.json depuis le cwd ; on force aussi OPENCODE_CONFIG.
        env["OPENCODE_CONFIG"] = str(workspace / "opencode.json")
        env.setdefault("CI", "1")  # signale un contexte non interactif
        # PWD : `opencode run` resout son repertoire de session depuis $PWD, pas depuis le
        # cwd reel du process. subprocess(cwd=...) change le cwd mais laisse PWD herite du
        # lanceur, donc l'agent travaillait dans le depot du harnais et non dans la cellule
        # (run bench-20260908-134041 : les 30 cellules ont commite dans onyxia-agent-bench,
        # workspaces vides, tous les scores a 0). Redondant avec --dir ci-dessous, garde
        # parce que les deux corrigent le probleme independamment (verifie empiriquement).
        env["PWD"] = str(workspace)
        # --agent build : sans lui, opencode retombe sur `default_agent` (souvent "plan" dans
        # la config globale onyxia, qui REFUSE toute edition - cf. README) et aucune tache ne
        # peut jamais rien ecrire.
        # --dir : indique explicitement a opencode le repertoire de la cellule. Sans lui,
        # `cwd=workspace` ne suffit pas (cf. PWD ci-dessus) et l'agent lit, ecrit et commite
        # dans le depot du harnais.
        cmd = [self.binary, "run", "--dir", str(workspace),
               "--agent", "build", "-m", model, "--format", "json",
               task.prompt]
        # XDG_CONFIG_HOME/XDG_DATA_HOME isoles : sans ca, opencode lit la vraie install
        # opencode-onyxia globale de la machine (~/.config/opencode : prompts, skills,
        # sous-agents, permissions) pour TOUTES les configs y compris C0, ce qui rend
        # l'echelle d'ablation inoperante (verifie empiriquement). Applique uniformement a
        # C0..C4 : seuls nos propres calques (configs/layers/) doivent varier.
        xdg_dir = tempfile.mkdtemp(prefix="bench-xdg-")
        env["XDG_CONFIG_HOME"] = str(Path(xdg_dir) / "config")
        env["XDG_DATA_HOME"] = str(Path(xdg_dir) / "data")
        t0 = time.time()
        timed_out = False
        rc = 0
        out, err = "", ""
        try:
            try:
                r = subprocess.run(cmd, cwd=workspace, env=env, capture_output=True,
                                   text=True, timeout=task.timeout_s)
                rc, out, err = r.returncode, r.stdout, r.stderr
            except subprocess.TimeoutExpired as e:
                timed_out = True
                rc = 124
                # TimeoutExpired.stdout/stderr peuvent rester en bytes meme avec text=True.
                def _decode(x: bytes | str | None) -> str:
                    return x.decode("utf-8", errors="replace") if isinstance(x, bytes) else (x or "")
                out = _decode(e.stdout)
                err = _decode(e.stderr) + "\n[TIMEOUT]"
            except FileNotFoundError:
                return RunResult(task.id, config_id, model, seed, workspace,
                                 Transcript(text=""), exit_code=127, wall_clock_s=0.0,
                                 error=f"binaire '{self.binary}' introuvable (opencode installe ?)")
        finally:
            shutil.rmtree(xdg_dir, ignore_errors=True)
        dt = time.time() - t0
        if rc == 124:
            timed_out = True
        transcript = parse_output(out)
        if err:
            transcript.raw_stdout += "\n[STDERR]\n" + err
        res = RunResult(task.id, config_id, model, seed, workspace, transcript,
                        exit_code=rc, timed_out=timed_out, wall_clock_s=dt,
                        error=None if rc == 0 else f"exit={rc}")
        res.agent_s = dt
        return res


# --------------------------------------------------------------------------------------
class PodOpenCodeDriver(BaseDriver):
    """Isolation par Job/pod Kubernetes ephemere : le systeme de fichiers du conteneur est
    la frontiere d'isolation (rien d'autre que le workspace pousse n'y existe), contrairement
    a RealOpenCodeDriver qui partage le systeme de fichiers de l'hote. Safe a appeler en
    concurrence (bench/runner.py le fait via --workers) : aucun etat mutable partage entre
    appels si ce n'est `_counter`, deja atomique en CPython (et combine a un uuid pour le
    nom du Job).

    `run()` ne leve jamais : toute defaillance (pod pas Ready, binaire manquant, exec en
    echec/timeout) est encodee dans le RunResult retourne, comme RealOpenCodeDriver le fait
    deja pour FileNotFoundError/TimeoutExpired - run_cell() ne rattrape rien autour de
    driver.run().
    """
    name = "pod"
    # Points de variation par agent (cf. PodClaudeDriver) : binaire a verifier dans le pod,
    # preparation du pod avant le push, commande exec, parseur de la sortie.
    agent_binary = "opencode"

    def __init__(self, image: str, namespace: str, secret_name: str, run_id: str,
                resources: dict, pod_workdir: str = "/tmp/bench-cell",
                ready_timeout_s: int = 180, ready_retries: int = 1):
        self.image = image
        self.namespace = namespace
        self.secret_name = secret_name
        self.run_id = run_id
        self.resources = resources
        self.pod_workdir = pod_workdir
        self.ready_timeout_s = ready_timeout_s
        self.ready_retries = ready_retries
        self._counter = itertools.count()

    def run(self, task, workspace, model, seed, config_id) -> RunResult:
        t0 = time.time()

        def fail(error: str, exit_code: int = 1, timed_out: bool = False) -> RunResult:
            return RunResult(task.id, config_id, model, seed, workspace, Transcript(text=""),
                             exit_code=exit_code, timed_out=timed_out,
                             wall_clock_s=time.time() - t0, error=error)

        # Le workspace est `<cell_dir>/ws` : les diagnostics k8s vont a cote, pas dedans
        # (le workspace est note et uploade tel quel).
        diag_path = workspace.parent / "k8s_failure.txt"
        last_err = None
        for attempt in range(self.ready_retries + 1):
            idx = next(self._counter)
            job_name = (f"bench-{k8s.sanitize_label(self.run_id)[:20]}-{idx:04d}-"
                        f"{uuid.uuid4().hex[:6]}")
            try:
                sleep_s = task.timeout_s + 300  # marge pour ready/push/pull autour de l'exec
                manifest = k8s.build_job_manifest(
                    name=job_name, namespace=self.namespace, image=self.image,
                    secret_name=self.secret_name, run_id=self.run_id, task_id=task.id,
                    config_id=config_id, seed=seed, model=model, sleep_seconds=sleep_s,
                    active_deadline_s=sleep_s, ttl_after_finished_s=120,
                    resources=self.resources)
                try:
                    k8s.kubectl_apply(manifest, timeout=30)
                except RuntimeError as e:
                    return fail(f"creation du job k8s echouee : {e}", exit_code=1)

                try:
                    pod = k8s.wait_pod_ready(job_name, self.namespace, self.ready_timeout_s)
                except (RuntimeError, subprocess.TimeoutExpired) as e:
                    # Capturer la cause AVANT le delete du finally (describe/events disparaissent
                    # avec le Job) : sans ca, "pod jamais pret" est indiagnosticable (34 cellules
                    # perdues sur un run reel sans aucune trace de la raison).
                    last_err = f"pod jamais pret : {e}"
                    try:
                        diag = k8s.diagnose_job(job_name, self.namespace)
                        with diag_path.open("a", encoding="utf-8") as fh:
                            fh.write(f"=== tentative {attempt + 1} job={job_name} : {last_err}\n"
                                     f"{diag}\n")
                    except Exception as de:  # diagnostic best-effort
                        last_err += f" (diagnostic k8s impossible : {de!r})"
                    if attempt < self.ready_retries:
                        k8s.delete("job", job_name, self.namespace, wait=True, timeout=PULL_TIMEOUT_S)
                        continue
                    return fail(last_err, exit_code=1)

                return self._exec_cell(task, workspace, model, seed, config_id, pod, t0)
            except Exception as e:  # filet de securite : run_cell() ne rattrape rien
                return fail(f"pod driver, erreur inattendue : {e!r}")
            finally:
                # Attendre la disparition du pod : sinon il chevauche la cellule suivante du
                # meme worker et le nombre reel de pods depasse --workers.
                k8s.delete("job", job_name, self.namespace, wait=True, timeout=90)
        return fail(last_err or "pod jamais pret", exit_code=1)

    def prepare_pod(self, pod: str, workspace: Path) -> str | None:
        """Prepare le pod avant le push (installer l'agent...). Renvoie un message d'erreur, ou
        None si tout va bien. Par defaut : verifier que le binaire de l'agent est dans l'image.
        Hors de `agent_s` : cette duree reste dans `wall_clock_s` seulement."""
        if not k8s.check_binary(pod, self.namespace, self.agent_binary):
            return (f"'{self.agent_binary}' absent de l'image '{self.image}' "
                    "(image mal choisie pour --isolation pod ?)")
        return None

    def exec_command(self, task, model) -> str:
        """Commande shell executee dans le pod par `kubectl exec ... sh -c`."""
        # --agent build : sans lui, opencode retombe sur `default_agent` (souvent "plan"
        # dans la config globale onyxia, qui REFUSE toute edition - cf. README).
        # XDG_CONFIG_HOME/XDG_DATA_HOME isoles : l'image bake ~/.config/opencode (la vraie
        # install opencode-onyxia globale - prompts, skills, sous-agents, permissions), qui
        # sinon s'applique a CHAQUE cellule quelle que soit sa config (C0 y compris) et
        # rend l'echelle d'ablation inoperante (verifie empiriquement). Seuls nos propres
        # calques (configs/layers/) doivent varier entre C0..C4.
        # Hors de pod_workdir expres : ce dossier est pousse/rapatrie tel quel, on ne
        # veut pas que ces fichiers d'etat opencode polluent le workspace note/uploade.
        # CI=1 : meme signal de non-interactivite que le driver process.
        xdg_cfg = shlex.quote("/tmp/bench-xdg-config")
        xdg_data = shlex.quote("/tmp/bench-xdg-data")
        return (f"cd {shlex.quote(self.pod_workdir)} && "
                f"OPENCODE_CONFIG={shlex.quote(self.pod_workdir + '/opencode.json')} "
                f"XDG_CONFIG_HOME={xdg_cfg} XDG_DATA_HOME={xdg_data} CI=1 "
                f"timeout {task.timeout_s}s opencode run --agent build "
                f"-m {shlex.quote(model)} --format json {shlex.quote(task.prompt)}")

    def parse(self, out: str) -> Transcript:
        return parse_output(out)

    def _exec_cell(self, task, workspace, model, seed, config_id, pod: str,
                   t0: float) -> RunResult:
        def fail(error: str, exit_code: int = 1) -> RunResult:
            return RunResult(task.id, config_id, model, seed, workspace, Transcript(text=""),
                             exit_code=exit_code, wall_clock_s=time.time() - t0, error=error)

        if not k8s.check_binary(pod, self.namespace, "tar"):
            return fail(f"'tar' absent de l'image '{self.image}' "
                        "(image mal choisie pour --isolation pod ?)", exit_code=127)
        prep_err = self.prepare_pod(pod, workspace)
        if prep_err:
            return fail(prep_err, exit_code=127)

        try:
            k8s.push_workspace(workspace, pod, self.namespace, self.pod_workdir, timeout=60)
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            return fail(f"push du workspace echoue : {e}")

        cmd = self.exec_command(task, model)
        timed_out = False
        rc = 0
        out, err = "", ""
        agent_t0 = time.time()
        try:
            r = subprocess.run(
                ["kubectl", "exec", pod, "-n", self.namespace, "--", "sh", "-c", cmd],
                capture_output=True, text=True, timeout=task.timeout_s + 30)
            rc, out, err = r.returncode, r.stdout, r.stderr
        except subprocess.TimeoutExpired as e:
            timed_out = True
            rc = 124

            def _decode(x):
                return x.decode("utf-8", errors="replace") if isinstance(x, bytes) else (x or "")
            out, err = _decode(e.stdout), _decode(e.stderr)
        agent_s = time.time() - agent_t0
        if rc == 124:
            # `timeout` (coreutils, dans le conteneur) rend 124 : c'est LE cas normal de
            # depassement en mode pod, le TimeoutExpired cote client n'arrive qu'en secours.
            timed_out = True
            err += "\n[TIMEOUT]"

        try:
            k8s.pull_workspace(pod, self.namespace, self.pod_workdir, workspace, timeout=60)
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            # on garde quand meme la sortie de l'exec : mieux vaut un grade partiel
            # (fichiers non recuperes) qu'une cellule totalement perdue.
            err += f"\n{PULL_FAILED_MARKER} {e}"

        transcript = self.parse(out)
        if err:
            transcript.raw_stdout += "\n[STDERR]\n" + err
        res = RunResult(task.id, config_id, model, seed, workspace, transcript,
                        exit_code=rc, timed_out=timed_out, wall_clock_s=time.time() - t0,
                        error=(f"exit={rc}" if rc != 0 else "pull workspace echoue"
                               if PULL_FAILED_MARKER in err else None))
        res.agent_s = agent_s  # duree de l'appel agent seul (hors cycle de vie du pod)
        return res


# --------------------------------------------------------------------------------------
class MockOpenCodeDriver(BaseDriver):
    """Stub deterministe : bon comportement en C3/C4, mauvais en C0.

    Sert UNIQUEMENT a valider le pipeline (boucle, isolation, notation, MLflow).
    Ne represente pas la performance reelle d'un modele.
    """
    name = "mock"

    def run(self, task, workspace, model, seed, config_id) -> RunResult:
        good = config_id in ("C3", "C4") or "agents_md" in config_id
        events: list[Event] = []
        t0 = time.time()

        if task.id == "t01_s3_parquet":
            if good:
                (workspace / "pipeline.py").write_text(_T01_GOOD, encoding="utf-8")
                events += [Event("message", "Je lis directement depuis S3 en memoire."),
                           Event("tool", name="write", text="pipeline.py")]
            else:
                (workspace / "pipeline.py").write_text(_T01_BAD, encoding="utf-8")
                events += [Event("message", "Je telecharge le dataset en local puis j'agrege."),
                           Event("tool", name="write", text="pipeline.py")]

        elif task.id == "t04_py_scaffold":
            if good:
                _write_good_scaffold(workspace)
                events += [Event("message", "Projet uv + ruff + pytest initialise."),
                           Event("tool", name="bash", text="uv init")]
            else:
                (workspace / "requirements.txt").write_text("pandas\nnumpy\n", encoding="utf-8")
                (workspace / "analyse.py").write_text("import pandas as pd\n", encoding="utf-8")
                events += [Event("message", "J'ai cree un requirements.txt et un script.")]

        elif task.id == "t10_diag_403":
            if good:
                events += [Event("message",
                    "Vous etiez operationnel hier et vous avez maintenant un 403 : sur la "
                    "plateforme, le jeton S3 expire au bout de 7 jours, c'est la cause la plus "
                    "probable. Renouvelez vos identifiants (rouvrez le service) et reessayez.")]
            else:
                events += [
                    Event("message", "Verifions d'abord la politique IAM du bucket."),
                    Event("tool", name="bash", text="check iam policy"),
                    Event("message", "Peut-etre un souci reseau ou de pare-feu ?"),
                    Event("tool", name="bash", text="ping endpoint"),
                    Event("message", "Je reecris le code de lecture S3 au cas ou."),
                ]
        else:
            events += [Event("message", f"[mock] tache {task.id} non specialisee.")]

        # tokens factices pour peupler les metriques
        for i, e in enumerate(events):
            e.turn = i + 1
        transcript = Transcript(
            text="\n".join(e.text for e in events if e.type == "message"),
            events=events, raw_stdout="[mock]",
            tokens_in=1200 if good else 2200, tokens_out=300 if good else 900,
            assistant_turns=len(events), context_tokens_first=800, context_tokens_last=1100)
        changed = [str(p.relative_to(workspace)) for p in workspace.rglob("*")
                   if p.is_file() and ".git" not in p.parts]
        return RunResult(task.id, config_id, model, seed, workspace, transcript,
                         exit_code=0, wall_clock_s=time.time() - t0, files_changed=changed)


# --- gabarits de code produits par le mock ---
_T01_GOOD = '''\
"""Revenu median par departement + top 10 communes, depuis S3 (ou un miroir local)."""
import os

import pandas as pd
import pyarrow.dataset as ds

CENSUS_URI = os.environ.get("CENSUS_URI", "s3://mon-bucket/data/census/")
OUTPUT_URI = os.environ.get("OUTPUT_URI", "s3://mon-bucket/data/derived/")


def storage_options():
    if not CENSUS_URI.startswith("s3://"):
        return None
    return {"key": os.environ["AWS_ACCESS_KEY_ID"], "secret": os.environ["AWS_SECRET_ACCESS_KEY"],
            "client_kwargs": {"endpoint_url": "https://" + os.environ["AWS_S3_ENDPOINT"]}}


def main():
    if CENSUS_URI.startswith("s3://"):
        df = pd.read_parquet(CENSUS_URI, storage_options=storage_options())  # lecture en memoire
    else:
        df = ds.dataset(CENSUS_URI, format="parquet", partitioning="hive").to_table().to_pandas()
    med = df.groupby("departement")["revenu_disponible"].median().reset_index()
    top = df.nlargest(10, "population")[["commune", "departement", "population"]]
    os.makedirs(OUTPUT_URI, exist_ok=True) if not OUTPUT_URI.startswith("s3://") else None
    med.to_parquet(OUTPUT_URI.rstrip("/") + "/revenu_median_departement.parquet", index=False,
                   storage_options=storage_options())
    top.to_parquet(OUTPUT_URI.rstrip("/") + "/top10_communes.parquet", index=False,
                   storage_options=storage_options())


if __name__ == "__main__":
    main()
'''

_T01_BAD = '''\
import boto3

s3 = boto3.client("s3", endpoint_url="https://minio.example.com",
                  aws_access_key_id="AKIAABCDEFGHIJKLMNOP",
                  aws_secret_access_key="s3cr3t-hardcoded-key-value")
s3.download_file("mon-bucket", "data/census/part-000.parquet", "/tmp/census.parquet")

import pandas as pd
df = pd.read_parquet("/tmp/census.parquet")
df.groupby("departement")["revenu_disponible"].median().to_csv("/tmp/out.csv")
'''


def _write_good_scaffold(ws: Path):
    (ws / "pyproject.toml").write_text(
        '[project]\nname = "analyse"\nversion = "0.1.0"\n'
        'requires-python = ">=3.11"\ndependencies = ["polars==1.9.0", "duckdb==1.1.1"]\n\n'
        '[tool.ruff]\nline-length = 100\n', encoding="utf-8")
    (ws / "uv.lock").write_text("# lockfile (stub)\n", encoding="utf-8")
    (ws / ".gitignore").write_text("data/\n*.env\n.env\n__pycache__/\n.venv/\n",
                                   encoding="utf-8")
    (ws / "tests").mkdir(exist_ok=True)
    (ws / "tests" / "test_smoke.py").write_text(
        "def test_smoke():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    (ws / "src").mkdir(exist_ok=True)
    (ws / "src" / "analyse.py").write_text("def run():\n    return True\n", encoding="utf-8")
