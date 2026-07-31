"""Pilotage d'OpenCode.

RealOpenCodeDriver : lance `opencode run -m <provider/model> --format json "<prompt>"`
dans le workspace isole, capture la sortie et la transforme en Transcript. Isolation
"legere" : sous-repertoire + git sur le systeme de fichiers de l'hote (voir les limites
connues du README - les outils d'OpenCode ne respectent pas forcement `cwd`).

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
from bench.schema import Event, RunResult, TaskSpec, Transcript


# --------------------------------------------------------------------------------------
# Parsing tolerant de la sortie `--format json` (le schema varie selon les versions).
# --------------------------------------------------------------------------------------
def parse_output(stdout: str) -> Transcript:
    t = Transcript(raw_stdout=stdout)
    stdout = (stdout or "").strip()
    if not stdout:
        return t

    def ingest_obj(obj: dict):
        # texte assistant
        role = obj.get("role")
        txt = obj.get("text") or obj.get("content") or obj.get("message") or ""
        if isinstance(txt, list):  # certains formats: content = [{type,text},...]
            txt = " ".join(p.get("text", "") for p in txt if isinstance(p, dict))
        if txt and role in (None, "assistant", "model"):
            t.events.append(Event("message", text=str(txt), raw=obj))
        # appels d'outils
        tname = obj.get("tool") or obj.get("tool_name") or obj.get("name")
        if obj.get("type") in ("tool", "tool_use", "tool_call") or (tname and "input" in obj):
            t.events.append(Event("tool", name=str(tname or "tool"),
                                  text=json.dumps(obj.get("input", {}))[:300], raw=obj))
        # usage tokens
        usage = obj.get("usage") or obj.get("tokens") or {}
        if isinstance(usage, dict):
            t.tokens_in += int(usage.get("input", usage.get("prompt_tokens", 0)) or 0)
            t.tokens_out += int(usage.get("output", usage.get("completion_tokens", 0)) or 0)
        # recursion sur des listes imbriquees frequentes
        for key in ("parts", "messages", "events", "steps", "output"):
            sub = obj.get(key)
            if isinstance(sub, list):
                for s in sub:
                    if isinstance(s, dict):
                        ingest_obj(s)

    # 1) objet JSON unique
    try:
        obj = json.loads(stdout)
        if isinstance(obj, dict):
            ingest_obj(obj)
        elif isinstance(obj, list):
            for s in obj:
                if isinstance(s, dict):
                    ingest_obj(s)
    except json.JSONDecodeError:
        # 2) nd-JSON (une ligne = un objet)
        parsed_any = False
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ingest_obj(json.loads(line))
                parsed_any = True
            except json.JSONDecodeError:
                pass
        # 3) repli texte brut
        if not parsed_any:
            t.events.append(Event("message", text=stdout))

    t.text = "\n".join(e.text for e in t.events if e.type == "message") or stdout
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
        env.update(self.extra_env)
        # opencode lit opencode.json depuis le cwd ; on force aussi OPENCODE_CONFIG.
        env["OPENCODE_CONFIG"] = str(workspace / "opencode.json")
        env.setdefault("CI", "1")  # signale un contexte non interactif
        # --agent build : sans lui, opencode retombe sur `default_agent` (souvent "plan" dans
        # la config globale onyxia, qui REFUSE toute edition - cf. README) et aucune tache ne
        # peut jamais rien ecrire.
        cmd = [self.binary, "run", "--agent", "build", "-m", model, "--format", "json",
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
        transcript = parse_output(out)
        if err:
            transcript.raw_stdout += "\n[STDERR]\n" + err
        return RunResult(task.id, config_id, model, seed, workspace, transcript,
                         exit_code=rc, timed_out=timed_out, wall_clock_s=dt,
                         error=None if rc == 0 else f"exit={rc}")


# --------------------------------------------------------------------------------------
class PodOpenCodeDriver(BaseDriver):
    """Isolation par Job/pod Kubernetes ephemere : le systeme de fichiers du conteneur est
    la frontiere d'isolation (rien d'autre que le workspace pousse n'y existe), contrairement
    a RealOpenCodeDriver qui partage le systeme de fichiers de l'hote. Sequentiel (un
    Job a la fois) - la parallelisation est un suivi delibere, pas construite ici.

    `run()` ne leve jamais : toute defaillance (pod pas Ready, binaire manquant, exec en
    echec/timeout) est encodee dans le RunResult retourne, comme RealOpenCodeDriver le fait
    deja pour FileNotFoundError/TimeoutExpired - run_cell() ne rattrape rien autour de
    driver.run().
    """
    name = "pod"

    def __init__(self, image: str, namespace: str, secret_name: str, run_id: str,
                resources: dict, pod_workdir: str = "/tmp/bench-cell",
                ready_timeout_s: int = 180):
        self.image = image
        self.namespace = namespace
        self.secret_name = secret_name
        self.run_id = run_id
        self.resources = resources
        self.pod_workdir = pod_workdir
        self.ready_timeout_s = ready_timeout_s
        self._counter = itertools.count()

    def run(self, task, workspace, model, seed, config_id) -> RunResult:
        t0 = time.time()
        idx = next(self._counter)
        job_name = f"bench-{k8s.sanitize_label(self.run_id)[:20]}-{idx:04d}-{uuid.uuid4().hex[:6]}"
        pod = None

        def fail(error: str, exit_code: int = 1, timed_out: bool = False) -> RunResult:
            return RunResult(task.id, config_id, model, seed, workspace, Transcript(text=""),
                             exit_code=exit_code, timed_out=timed_out,
                             wall_clock_s=time.time() - t0, error=error)

        try:
            sleep_s = task.timeout_s + 300  # marge pour ready/push/pull autour de l'exec
            manifest = k8s.build_job_manifest(
                name=job_name, namespace=self.namespace, image=self.image,
                secret_name=self.secret_name, run_id=self.run_id, task_id=task.id,
                config_id=config_id, seed=seed, model=model, sleep_seconds=sleep_s,
                active_deadline_s=sleep_s, ttl_after_finished_s=120, resources=self.resources)
            try:
                k8s.kubectl_apply(manifest, timeout=30)
            except RuntimeError as e:
                return fail(f"creation du job k8s echouee : {e}", exit_code=1)

            try:
                pod = k8s.wait_pod_ready(job_name, self.namespace, self.ready_timeout_s)
            except (RuntimeError, subprocess.TimeoutExpired) as e:
                return fail(f"pod jamais pret : {e}", exit_code=1)

            for binary in ("tar", "opencode"):
                if not k8s.check_binary(pod, self.namespace, binary):
                    return fail(f"'{binary}' absent de l'image '{self.image}' "
                               "(image mal choisie pour --isolation pod ?)", exit_code=127)

            try:
                k8s.push_workspace(workspace, pod, self.namespace, self.pod_workdir,
                                  timeout=60)
            except (RuntimeError, subprocess.TimeoutExpired) as e:
                return fail(f"push du workspace echoue : {e}")

            # --agent build : sans lui, opencode retombe sur `default_agent` (souvent "plan"
            # dans la config globale onyxia, qui REFUSE toute edition - cf. README).
            # XDG_CONFIG_HOME/XDG_DATA_HOME isoles : l'image bake ~/.config/opencode (la vraie
            # install opencode-onyxia globale - prompts, skills, sous-agents, permissions), qui
            # sinon s'applique a CHAQUE cellule quelle que soit sa config (C0 y compris) et
            # rend l'echelle d'ablation inoperante (verifie empiriquement). Seuls nos propres
            # calques (configs/layers/) doivent varier entre C0..C4.
            # Hors de pod_workdir expres : ce dossier est pousse/rapatrie tel quel, on ne
            # veut pas que ces fichiers d'etat opencode polluent le workspace note/uploade.
            xdg_cfg = shlex.quote("/tmp/bench-xdg-config")
            xdg_data = shlex.quote("/tmp/bench-xdg-data")
            cmd = (f"cd {shlex.quote(self.pod_workdir)} && "
                  f"OPENCODE_CONFIG={shlex.quote(self.pod_workdir + '/opencode.json')} "
                  f"XDG_CONFIG_HOME={xdg_cfg} XDG_DATA_HOME={xdg_data} "
                  f"timeout {task.timeout_s}s opencode run --agent build "
                  f"-m {shlex.quote(model)} --format json {shlex.quote(task.prompt)}")
            timed_out = False
            rc = 0
            out, err = "", ""
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
                out, err = _decode(e.stdout), _decode(e.stderr) + "\n[TIMEOUT]"

            try:
                k8s.pull_workspace(pod, self.namespace, self.pod_workdir, workspace,
                                  timeout=60)
            except (RuntimeError, subprocess.TimeoutExpired) as e:
                # on garde quand meme la sortie de l'exec : mieux vaut un grade partiel
                # (fichiers non recuperes) qu'une cellule totalement perdue.
                err += f"\n[PULL WORKSPACE ECHOUE] {e}"

            transcript = parse_output(out)
            if err:
                transcript.raw_stdout += "\n[STDERR]\n" + err
            return RunResult(task.id, config_id, model, seed, workspace, transcript,
                             exit_code=rc, timed_out=timed_out, wall_clock_s=time.time() - t0,
                             error=None if rc == 0 else f"exit={rc}")
        except Exception as e:  # filet de securite : run_cell() ne rattrape rien
            return fail(f"pod driver, erreur inattendue : {e!r}")
        finally:
            k8s.delete("job", job_name, self.namespace)


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
        transcript = Transcript(
            text="\n".join(e.text for e in events if e.type == "message"),
            events=events, raw_stdout="[mock]",
            tokens_in=1200 if good else 2200, tokens_out=300 if good else 900)
        changed = [str(p.relative_to(workspace)) for p in workspace.rglob("*")
                   if p.is_file() and ".git" not in p.parts]
        return RunResult(task.id, config_id, model, seed, workspace, transcript,
                         exit_code=0, wall_clock_s=time.time() - t0, files_changed=changed)


# --- gabarits de code produits par le mock ---
_T01_GOOD = '''\
import os
import duckdb

con = duckdb.connect()
con.execute("INSTALL httpfs; LOAD httpfs;")
con.execute(f"SET s3_endpoint=\\'{os.environ[\\'AWS_S3_ENDPOINT\\']}\\';")
con.execute(f"SET s3_access_key_id=\\'{os.environ[\\'AWS_ACCESS_KEY_ID\\']}\\';")
con.execute(f"SET s3_secret_access_key=\\'{os.environ[\\'AWS_SECRET_ACCESS_KEY\\']}\\';")

df = con.execute("""
    SELECT departement, median(revenu_disponible) AS revenu_median
    FROM read_parquet('s3://mon-bucket/data/census/*.parquet')
    GROUP BY departement
""").pl()

con.execute("""
    COPY (SELECT commune, population FROM read_parquet('s3://mon-bucket/data/census/*.parquet')
          ORDER BY population DESC LIMIT 10)
    TO 's3://mon-bucket/data/derived/top10_communes.parquet' (FORMAT PARQUET)
""")
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
