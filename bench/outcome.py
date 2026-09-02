"""Helpers de notation *sur resultat* (par opposition aux motifs regex de `bench.grading`).

Schema commun aux tasks t13+ : les livrables sont supprimes, le script de l'agent est
re-execute dans le workspace avec un environnement controle, puis les sorties sont comparees
a une verite terrain (`ground_truth.json` dans le dossier de la task, hors `fixtures/`, donc
invisible pour l'agent). Toutes les comparaisons rendent un credit partiel.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import resource
import subprocess
from pathlib import Path

from bench.grading import Check, _iter_files

# --------------------------------------------------------------------------- fichiers


def load_truth(task_file: str) -> dict:
    return json.loads((Path(task_file).parent / "ground_truth.json").read_text(encoding="utf-8"))


def find(ws: Path, name: str) -> Path | None:
    """Premier fichier `name` (glob autorise) hors .git / caches."""
    for p in ws.rglob(name):
        if ".git" in p.parts or ".venv" in p.parts or "node_modules" in p.parts:
            continue
        if p.is_file():
            return p
    return None


def remove_outputs(ws: Path, names: list[str]) -> None:
    for n in names:
        for p in list(ws.rglob(n)):
            if ".git" not in p.parts and p.is_file():
                p.unlink()


# --------------------------------------------------------------------------- execution


def candidate_scripts(ws: Path, exts: tuple[str, ...] = ("*.py",)) -> list[Path]:
    """Scripts hors tests, racine d'abord, noms evocateurs d'abord."""
    py = [p for p in _iter_files(ws, list(exts))
          if not p.name.startswith("test_") and "tests" not in p.parts
          and not p.name.startswith("conftest") and p.name != "setup.py"]

    def rank(p: Path) -> tuple:
        n = p.stem.lower()
        hint = any(w in n for w in ("main", "run", "pipeline", "analy", "valid", "train",
                                    "agreg", "aggreg", "etl", "process", "fix", "publish",
                                    "ingest", "build", "compute", "count", "prepare"))
        return (len(p.relative_to(ws).parts), not hint, n)
    return sorted(py, key=rank)


def run_script(ws: Path, script: Path, *, timeout: int = 180, env: dict | None = None,
               mem_limit_mb: int | None = None) -> dict:
    """Execute `script` (uv run --frozen si lockfile, sinon python). Renvoie
    {ok, detail, peak_rss_mb}. `mem_limit_mb` pose un RLIMIT_AS dans l'enfant."""
    full_env = {**os.environ, **(env or {})}
    cmds = [["python", str(script)]]
    if (ws / "pyproject.toml").exists() and (ws / "uv.lock").exists():
        cmds.insert(0, ["uv", "run", "--frozen", "python", str(script)])

    def preexec():
        if mem_limit_mb:
            lim = mem_limit_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (lim, lim))

    last = "aucun interpreteur"
    for cmd in cmds:
        before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        try:
            r = subprocess.run(cmd, cwd=ws, capture_output=True, text=True, timeout=timeout,
                               env=full_env, preexec_fn=preexec)
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return {"ok": False, "detail": f"timeout {timeout}s", "peak_rss_mb": None}
        peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024  # kB -> MB (linux)
        if r.returncode == 0:
            return {"ok": True, "detail": f"ok ({cmd[0]})", "peak_rss_mb": max(peak, before / 1024)}
        tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or [""]
        last = f"{cmd[0]}: {tail[0][:160]}"
    return {"ok": False, "detail": last, "peak_rss_mb": None}


def reexecute(ws: Path, outputs: list[str], *, name: str = "script_reexecutes",
              timeout: int = 180, env: dict | None = None, mem_limit_mb: int | None = None,
              max_candidates: int = 3) -> tuple[Check, dict]:
    """Supprime `outputs`, tente les scripts candidats jusqu'a ce que tous les livrables
    existent. Renvoie (Check axe repro, info du run reussi ou dernier essai)."""
    remove_outputs(ws, outputs)
    scripts = candidate_scripts(ws)
    if not scripts:
        return Check(name, False, 0.0, axis="repro", detail="aucun script .py"), {}
    info: dict = {}
    for s in scripts[:max_candidates]:
        info = run_script(ws, s, timeout=timeout, env=env, mem_limit_mb=mem_limit_mb)
        if info["ok"] and all(find(ws, o) for o in outputs):
            info["script"] = str(s.relative_to(ws))
            return Check(name, True, 1.0, axis="repro",
                         detail=f"{info['script']} : {info['detail']}"), info
        if info["ok"]:
            missing = [o for o in outputs if not find(ws, o)]
            info["detail"] = f"{s.name} tourne mais ne produit pas {missing}"
    return Check(name, False, 0.0, axis="repro", detail=info.get("detail", "")), info


# --------------------------------------------------------------------------- lecture tolerante


def read_table(p: Path) -> list[dict]:
    """CSV (sep/encodage devines) ou Parquet -> liste de dicts (valeurs en str pour CSV)."""
    if p.suffix.lower() in (".parquet", ".pq"):
        import pyarrow.parquet as pq
        return pq.read_table(p).to_pylist()
    raw = p.read_bytes()
    text = None
    for enc in ("utf-8-sig", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        return []
    try:
        dialect = csv.Sniffer().sniff(text[:4000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return list(csv.DictReader(io.StringIO(text), dialect=dialect))


def pick_column(cols: list[str], patterns: list[str], exclude: set[str] = frozenset()) -> str | None:
    for pat in patterns:
        for c in cols:
            if c and c not in exclude and re.search(pat, c, re.I):
                return c
    return None


def to_float(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("\u202f", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def keyed_values(rows: list[dict], key_patterns: list[str], val_patterns: list[str],
                 *, normalize_key=lambda s: s) -> dict[str, float]:
    """{cle: valeur} depuis une table dont on devine les colonnes cle / valeur."""
    if not rows:
        return {}
    cols = [c for c in rows[0].keys() if c is not None]
    kc = pick_column(cols, key_patterns) or cols[0]
    vc = pick_column(cols, val_patterns, exclude={kc})
    if vc is None:
        vc = next((c for c in cols if c != kc), None)
    out: dict[str, float] = {}
    for r in rows:
        k = normalize_key(str(r.get(kc, "")).strip().strip('"'))
        v = to_float(r.get(vc))
        if k and v is not None:
            out[k] = v
    return out


def compare_keyed(got: dict[str, float], truth: dict[str, float], *, name: str,
                  rel_tol: float = 0.01, abs_tol: float = 0.0, axis: str = "functional",
                  alt_key=lambda k: k) -> Check:
    """Credit partiel = fraction des cles de la verite terrain a +/- tolerance."""
    hits, misses = 0, []
    for k, ref in truth.items():
        v = got.get(k, got.get(alt_key(k)))
        if v is not None and abs(v - ref) <= max(rel_tol * abs(ref), abs_tol):
            hits += 1
        else:
            misses.append(f"{k}:{v}!={ref}")
    s = hits / max(1, len(truth))
    return Check(name, s >= 0.99, s, axis=axis,
                 detail=f"{hits}/{len(truth)} ok" + (f" ; ex. {misses[:3]}" if misses else ""))


def table_check(ws: Path, filename: str, truth: dict[str, float], *, name: str,
                key_patterns: list[str], val_patterns: list[str], rel_tol: float = 0.01,
                normalize_key=lambda s: s, alt_key=lambda k: k) -> list[Check]:
    p = find(ws, filename)
    if not p:
        return [Check(f"{name}_present", False, 0.0, detail=f"{filename} absent"),
                Check(name, False, 0.0, detail="fichier absent")]
    got = keyed_values(read_table(p), key_patterns, val_patterns, normalize_key=normalize_key)
    return [Check(f"{name}_present", bool(got), 1.0 if got else 0.0,
                  detail=f"{len(got)} ligne(s) lue(s)"),
            compare_keyed(got, truth, name=name, rel_tol=rel_tol, alt_key=alt_key)]


# --------------------------------------------------------------------------- JSON tolerant


def walk(o, path=()):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from walk(v, path + (str(k),))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from walk(v, path + (str(i),))
    else:
        yield path, o


def load_json(ws: Path, name: str):
    p = find(ws, name)
    if not p:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None


def json_strings(o) -> set[str]:
    """Toutes les chaines (feuilles + cles) d'un JSON, pour chercher des identifiants."""
    out = set()
    for path, v in walk(o):
        out.update(path)
        if isinstance(v, str):
            out.add(v)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            out.add(str(v))
    return out


def json_int_under(o, key_re: str) -> int | None:
    rx = re.compile(key_re, re.I)
    for path, v in walk(o):
        if path and rx.search(path[-1]) and isinstance(v, (int, float)) and not isinstance(v, bool):
            return int(v)
    return None


# --------------------------------------------------------------------------- git


def git_history_contains(ws: Path, needle: str) -> bool:
    """Le motif apparait-il dans un blob commite (toutes branches) ?"""
    r = subprocess.run(["git", "-C", str(ws), "log", "--all", "-p", "--", "."],
                       capture_output=True, text=True)
    return needle in r.stdout


def git_diff_stat_vs_initial(ws: Path, path: str) -> tuple[int, int]:
    """(lignes ajoutees, supprimees) sur `path` entre le commit des fixtures et le worktree."""
    first = subprocess.run(["git", "-C", str(ws), "rev-list", "--max-parents=0", "HEAD"],
                           capture_output=True, text=True).stdout.strip().splitlines()
    if not first:
        return (0, 0)
    r = subprocess.run(["git", "-C", str(ws), "diff", "--numstat", first[0], "--", path],
                       capture_output=True, text=True)
    add = dele = 0
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].isdigit():
            add += int(parts[0])
            dele += int(parts[1])
    return (add, dele)


# --------------------------------------------------------------------------- MLflow (store jetable)


def mlflow_runs(tracking_uri: str) -> list[dict]:
    """Runs (tous experiments) d'un store MLflow : {params, metrics, artifacts, tags}."""
    import mlflow
    from mlflow.tracking import MlflowClient
    mlflow.set_tracking_uri(tracking_uri)
    c = MlflowClient(tracking_uri)
    out = []
    for exp in c.search_experiments():
        models: dict[str, list[str]] = {}
        try:  # MLflow >= 3 : log_model cree un LoggedModel rattache au run, pas un artefact
            for m in c.search_logged_models(experiment_ids=[exp.experiment_id]):
                models.setdefault(m.source_run_id or "", []).append(f"model:{m.name}")
        except Exception:
            pass
        for r in c.search_runs([exp.experiment_id]):
            arts = list(models.get(r.info.run_id, []))
            try:
                arts += [a.path for a in c.list_artifacts(r.info.run_id)]
            except Exception:  # store sans artefacts accessibles
                pass
            out.append({"params": dict(r.data.params), "metrics": dict(r.data.metrics),
                        "tags": dict(r.data.tags), "artifacts": arts,
                        "experiment": exp.name})
    return out
