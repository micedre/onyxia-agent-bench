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
import re
import shutil
import subprocess
from pathlib import Path

from bench.grading import (
    Check,
    _is_layer_path,
    _layer_injected_files,
    deliverable_files,
    rank_entry_scripts,
    run_in_project,
)

# --------------------------------------------------------------------------- fichiers


def load_truth(task_file: str) -> dict:
    return json.loads((Path(task_file).parent / "ground_truth.json").read_text(encoding="utf-8"))


def find(ws: Path, name: str) -> Path | None:
    """Premier LIVRABLE nomme `name` (glob autorise), ordre deterministe.

    Delegue a `grading.deliverable_files`, donc exclut les fichiers deposes par les couches de
    config. Sans cette exclusion, un `rglob` brut peut renvoyer un fichier de skill : le
    gabarit `.opencode/skills/quarto-publication/assets/report-template.qmd` contient
    `params:`, ce qui faisait passer un check de parametrage en C2/C3/C4 alors que l'agent
    n'avait rien produit - un biais correle a la config mesuree.
    """
    files = deliverable_files(ws, [name])
    return files[0] if files else None


def find_all(ws: Path, name: str) -> list[Path]:
    return deliverable_files(ws, [name])


#: Repertoires jamais consideres comme un emplacement de sortie : depot git, environnements,
#: caches d'outils (ils peuvent contenir des fichiers de meme nom sans etre le travail de l'agent).
_OUTPUT_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache",
                     ".grade_venv", ".mypy_cache", ".ipynb_checkpoints"}


def find_outputs(ws: Path, name: str) -> list[Path]:
    """Fichiers de SORTIE nommes `name` (glob autorise), presents SUR DISQUE.

    Contrairement a `find`, un fichier ignore par git est vu. Un script qui regenere sa sortie
    la depose sur le disque : c'est son existence apres re-execution qui prouve la regeneration,
    pas sa visibilite pour git. Verifie sur de vraies cellules : les 10 cellules Opus de t18 et 7 des
    10 du 27B avaient `part_communes_sous_seuil.csv` dans leur `.gitignore` (la regle « pas de
    donnees dans Git » de la plateforme) et le grader les jugeait « absentes », ce qui plafonnait la
    tache pres de 0.47 quoi que fasse l'agent. A n'utiliser que pour les sorties DECLAREES : le code
    de l'agent reste cherche par `find`/`deliverable_files` (visibles par git), qui ecartent les
    fichiers de couche. Les fichiers de couche restent ecartes ici aussi.
    """
    ws = ws.resolve()
    injected = _layer_injected_files(ws)
    out = []
    for p in ws.rglob(name):
        rel = p.relative_to(ws)
        if (not p.is_file() or set(rel.parts) & _OUTPUT_SKIP_DIRS or _is_layer_path(rel)
                or p.resolve() in injected):
            continue
        out.append(p)
    out.sort(key=lambda p: (len(p.relative_to(ws).parts), str(p)))
    return out


def find_output(ws: Path, name: str) -> Path | None:
    files = find_outputs(ws, name)
    return files[0] if files else None


# --------------------------------------------------------------------------- execution


#: Repertoires qu'on ne recopie pas pour la reexecution (volumineux et regenerables).
_COPY_IGNORE = shutil.ignore_patterns(".venv", "__pycache__", "node_modules", "*.pyc",
                                      ".pytest_cache", ".ruff_cache", ".grade_venv")


def candidate_scripts(ws: Path, exts: tuple[str, ...] = ("*.py",),
                      exclude_names: tuple[str, ...] = ()) -> list[Path]:
    """Points d'entree plausibles, du plus probable au moins probable.

    `deliverable_files` ecarte deja les couches de config, les tests et les brouillons `_x.py` ;
    `rank_entry_scripts` prefere un `__main__`/`def main(`, puis un nom evocateur, puis la
    racine. `exclude_names` sert aux fixtures executables qu'il ne faut jamais lancer (un
    serveur de test lance comme candidat bloquerait jusqu'au timeout).
    """
    scripts = [p for p in deliverable_files(ws, list(exts)) if p.name not in exclude_names]
    return rank_entry_scripts(ws, scripts)


def _run_one(ws: Path, script: Path, *, timeout: int, env_extra: dict | None) -> dict:
    """Execute un script dans l'environnement du PROJET de l'agent (`uv run`), pas dans celui
    du harnais : un agent qui a fait `uv add duckdb` doit pouvoir etre note (c'est le faux
    negatif ModuleNotFoundError qui avait mis t04 `tests_pass` a 0/15). `run_in_project`
    resout aussi le workspace en absolu - un chemin relatif ferait pointer le script hors du
    cwd de l'enfant et mettrait a zero toute la notation d'un `bench regrade` en relatif."""
    rel = script.resolve().relative_to(ws.resolve())
    try:
        r, note = run_in_project(ws, ["python", str(rel)], timeout=timeout, env_extra=env_extra)
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": f"timeout {timeout}s", "script": str(rel)}
    except OSError as e:
        return {"ok": False, "detail": f"erreur: {e}", "script": str(rel)}
    if r.returncode == 0:
        return {"ok": True, "detail": f"ok [{note}]", "script": str(rel), "stdout": r.stdout}
    tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [""]
    return {"ok": False, "detail": f"{rel} [{note}] {tail[0][:160]}", "script": str(rel),
            "stdout": r.stdout}


def reexecute(ws: Path, outputs: list[str], *, name: str = "script_reexecutes",
              timeout: int = 180, env: dict | None = None, max_candidates: int = 2,
              exclude_names: tuple[str, ...] = (), exts: tuple[str, ...] = ("*.py",),
              axis: str = "repro") -> tuple[Check, dict]:
    """Verifie que les livrables se REGENERENT, sans jamais toucher au workspace note.

    Le workspace est copie a cote (`<cellule>/.grade_rerun`), les livrables sont supprimes
    DANS LA COPIE, puis les scripts candidats sont enchaines jusqu'a ce que tous les livrables
    existent (un agent a le droit de separer validation.py et agregats.py).

    Renvoie `(Check, info)` ou `info["ws"]` est le repertoire dans lequel lire les sorties :
    la copie si la reexecution a reussi, le workspace d'origine sinon - de sorte qu'un livrable
    correct garde le credit de son contenu meme si la reexecution echoue, et que deux notations
    successives de la meme cellule donnent le meme resultat.
    """
    ws = ws.resolve()
    rerun = ws.parent / ".grade_rerun"
    shutil.rmtree(rerun, ignore_errors=True)
    try:
        shutil.copytree(ws, rerun, ignore=_COPY_IGNORE, symlinks=True)
    except OSError as e:
        return (Check(name, False, 0.0, axis=axis, detail=f"copie du workspace impossible: {e}"),
                {"ws": ws})
    # Suppression SUR LE DISQUE (y compris les sorties ignorees par git) : sinon le CSV de l'agent
    # resterait dans la copie et la verification ci-dessous passerait a vide.
    for out in outputs:
        for p in find_outputs(rerun, out):
            p.unlink(missing_ok=True)

    scripts = candidate_scripts(rerun, exts, exclude_names)
    if not scripts:
        return (Check(name, False, 0.0, axis=axis,
                      detail=f"aucun script {list(exts)} : rien a reexecuter"), {"ws": ws})
    info: dict = {}
    tried = []
    for script in scripts[:max_candidates]:
        info = _run_one(rerun, script, timeout=timeout, env_extra=env)
        tried.append(info["detail"])
        if all(find_output(rerun, o) for o in outputs):
            info["ws"] = rerun
            return Check(name, True, 1.0, axis=axis,
                         detail=f"{info['script']} : {info['detail']}"), info
    missing = [o for o in outputs if not find_output(rerun, o)]
    info["ws"] = ws  # repli : on note ce que l'agent a livre, sans le detruire
    cause = _diagnose_rerun_failure(rerun, scripts[:max_candidates], tried)
    return (Check(name, False, 0.0, axis=axis,
                  detail=f"{cause} ; livrables manquants {missing}"), info)


#: Repertoires de travail dans lesquels l'agent tourne, mais qui n'existent plus a la notation.
#: Un script qui code l'un d'eux en dur ne tourne nulle part ailleurs.
_EXEC_WORKDIRS = ("/tmp/bench-cell",)


def _diagnose_rerun_failure(rerun: Path, scripts: list[Path], tried: list[str]) -> str:
    """Nomme la cause d'un echec de reexecution.

    Le score reste 0 dans tous les cas - un script qui ne tourne qu'a un chemin absolu n'est
    pas reproductible - mais sans cette distinction un lecteur ne peut pas separer "defaut du
    livrable" de "bug du harnais" sans ouvrir la cellule.
    """
    for script in scripts:
        try:
            txt = script.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hit = next((w for w in _EXEC_WORKDIRS if w in txt), None)
        if hit:
            return (f"chemin absolu du repertoire d'execution code en dur ({hit}) dans "
                    f"{script.name} : le script ne tourne que la ou l'agent l'a ecrit")
    return f"le script ne regenere pas les livrables ; essais: {tried}"


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
            if c and c not in exclude and re.search(pat, c, re.IGNORECASE):
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


def compare_with_alternatives(got: dict[str, float], truth: dict[str, float],
                              alternatives: dict[str, dict[str, float]], *, name: str,
                              cap: float = 0.5, zero_above: float | None = None,
                              weight: float = 1.0, axis: str = "functional",
                              rel_tol: float = 0.01, abs_tol: float = 0.0,
                              alt_key=lambda k: k) -> Check:
    """Compare a la verite terrain ET a des reponses fausses NOMMEES.

    Sans ca, une reponse fausse se resume a "0/13 ok" et il faut ouvrir le workspace pour
    savoir pourquoi : sur un run reel, trois cellules avaient invente une ponderation par la
    population et une avait garde la moyenne d'origine, quatre diagnostics indiscernables.

    Si le score contre la verite est imparfait et qu'une alternative colle mieux, le `Check`
    rend `cap x score de l'alternative` et son `detail` NOMME la cause. `zero_above` sert au
    cas ou une alternative qui colle presque partout ne merite aucun credit (t21 : mesurer une
    distance en degres donne "tout est a moins de 5 km", ce n'est pas une reponse partielle).
    """
    best = compare_keyed(got, truth, name=name, rel_tol=rel_tol, abs_tol=abs_tol,
                         axis=axis, alt_key=alt_key)
    best.weight = weight
    if best.score >= 0.99:
        return best
    for label, series in alternatives.items():
        alt = compare_keyed(got, series, name="_", rel_tol=rel_tol, abs_tol=abs_tol,
                            alt_key=alt_key)
        if alt.score <= best.score:
            continue
        if zero_above is not None and alt.score > zero_above:
            return Check(name, False, 0.0, weight=weight, axis=axis, detail=label)
        best = Check(name, False, round(cap * alt.score, 4), weight=weight, axis=axis,
                     detail=f"{label} ({alt.detail})")
    return best


def table_check(ws: Path, filename: str, truth: dict[str, float], *, name: str,
                key_patterns: list[str], val_patterns: list[str], rel_tol: float = 0.01,
                abs_tol: float = 0.0, weight: float = 1.0,
                normalize_key=lambda s: s, alt_key=lambda k: k) -> list[Check]:
    """Deux checks : la table est lisible, et ses valeurs correspondent a la verite terrain.

    Penser a resserrer `rel_tol`/`abs_tol` par tache : le defaut de 1 % est trop large pour
    des agregats, au point d'absorber le piege qu'on veut mesurer (sur t14, `mean` au lieu de
    `median` passait inapercu dans 9 departements sur 13)."""
    p = find_output(ws, filename)
    if not p:
        return [Check(f"{name}_present", False, 0.0, detail=f"{filename} absent"),
                Check(name, False, 0.0, weight=weight, detail="fichier absent")]
    got = keyed_values(read_table(p), key_patterns, val_patterns, normalize_key=normalize_key)
    cmp_check = compare_keyed(got, truth, name=name, rel_tol=rel_tol, abs_tol=abs_tol,
                              alt_key=alt_key)
    cmp_check.weight = weight
    return [Check(f"{name}_present", bool(got), 1.0 if got else 0.0,
                  detail=f"{len(got)} ligne(s) lue(s)"), cmp_check]


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
    rx = re.compile(key_re, re.IGNORECASE)
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
    from mlflow.tracking import MlflowClient
    # pas de mlflow.set_tracking_uri() : c'est un etat GLOBAL du processus, et le logger du
    # harnais tourne en meme temps. Le client prend l'URI explicitement.
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
