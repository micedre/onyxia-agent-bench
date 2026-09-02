"""T24 : ingestion d'API paginee avec cache. Le grader demarre lui-meme le serveur mock sur
un port libre, execute le script deux fois et lit le journal de requetes du serveur :
1er run -> toutes les pages ; 2e run -> 0 requete (cache local) ou uniquement des
revalidations conditionnelles (If-None-Match -> 304)."""
import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from bench.grading import code_text, env_vars_used, no_hardcoded_secrets
from bench.outcome import load_truth, reexecute, remove_outputs, find, read_table, run_script, candidate_scripts, Check

T = load_truth(__file__)
OUT = "data/pop_dep.parquet"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def grade(ctx):
    ws = ctx.workspace
    server = Path(__file__).parent / "fixtures" / "mock_stats_api.py"
    port = _free_port()
    log = server.with_name("requests.log")
    proc = subprocess.Popen([sys.executable, str(server), str(port)], cwd=ws,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    env = {"STATS_API_URL": f"http://127.0.0.1:{port}"}
    checks = []
    try:
        time.sleep(0.6)
        log.write_text("")
        for cache in ws.rglob("*"):  # repart d'un cache vide, sans toucher aux __pycache__
            if cache.is_dir() and ".git" not in cache.parts and cache.name in (".cache", "cache", "http_cache", ".http_cache", ".requests-cache"):
                shutil.rmtree(cache, ignore_errors=True)
        chk, info = reexecute(ws, [OUT], env=env)
        checks.append(chk)
        n1 = len(log.read_text().splitlines())
        p = find(ws, "pop_dep.parquet")
        n = len(read_table(p)) if p else 0
        ok = n == T["n_rows"]
        checks.append(Check("all_pages_ingested", ok, 1.0 if ok else (0.5 if 0 < n < T["n_rows"] else 0.0),
                            detail=f"{n} lignes (attendu {T['n_rows']}), {n1} requete(s) au 1er run"))
        if chk.passed and info.get("script"):
            log.write_text("")
            run_script(ws, ws / info["script"], env=env)
            reqs = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
            unconditional = [r for r in reqs if not r.get("if_none_match")]
            cached = not unconditional
            checks.append(Check("second_run_uses_cache", cached, 1.0 if cached else (0.5 if len(reqs) < n1 else 0.0),
                                axis="efficiency", detail=f"2e run : {len(reqs)} requete(s), {len(unconditional)} sans validation conditionnelle"))
    finally:
        proc.kill()
        log.unlink(missing_ok=True)
    text = code_text(ws, ["*.py"])
    checks.append(env_vars_used(text, ["STATS_API_URL"], name="reads_api_url_env"))
    checks.append(no_hardcoded_secrets(text))
    return checks
