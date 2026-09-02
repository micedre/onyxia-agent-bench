"""T19 : publication sur S3. Sans droits d'ecriture, on note le contenu prepare (Parquet
lisible au bon schema, README avec source/millesime, dictionnaire couvrant chaque colonne)
et le script (chemin `diffusion/` a la racine du bucket, identifiants via l'env, jamais de
telechargement inutile)."""
import re
from bench.grading import code_text, env_vars_used, no_hardcoded_secrets
from bench.outcome import load_truth, find, read_table, Check

T = load_truth(__file__)


def grade(ctx):
    ws = ctx.workspace
    checks = []
    pub = ws / "publish"
    pq = next((p for p in pub.rglob("*.parquet")), None) if pub.is_dir() else None
    if pq:
        try:
            rows = read_table(pq)
            cols = set(rows[0].keys()) if rows else set()
            ok = set(T["columns"]) <= cols and len(rows) == T["n_rows"]
            checks.append(Check("parquet_schema_ok", ok, 1.0 if ok else 0.5, axis="functional",
                                detail=f"colonnes={sorted(cols)}, lignes={len(rows)}"))
        except Exception as e:
            checks.append(Check("parquet_schema_ok", False, 0.0, detail=f"illisible: {e!r}"))
    else:
        checks.append(Check("parquet_schema_ok", False, 0.0, detail="aucun .parquet sous publish/"))
    readme = next((p for p in (pub.rglob("README*") if pub.is_dir() else [])), None)
    txt = readme.read_text(encoding="utf-8", errors="replace") if readme else ""
    has = [bool(re.search(r"(?i)filosofi", txt)), "2021" in txt, bool(re.search(r"2025|COG", txt)),
           bool(re.search(r"(?i)licen[cs]e|source", txt))]
    s = sum(has) / len(has)
    checks.append(Check("readme_documents_source", s >= 0.99, s, axis="repro",
                        detail=f"filosofi/2021/cog/licence = {has}"))
    dic = next((p for p in (pub.rglob("*") if pub.is_dir() else [])
                if re.search(r"(?i)dict|dictionnaire|schema|metadata", p.name) and p.is_file()), None)
    dtxt = dic.read_text(encoding="utf-8", errors="replace") if dic else ""
    covered = [c for c in T["columns"] if c in dtxt]
    s = len(covered) / len(T["columns"])
    checks.append(Check("dictionary_covers_columns", s >= 0.99, s, axis="repro",
                        detail=f"{len(covered)}/{len(T['columns'])} colonnes decrites"))
    text = code_text(ws, ["*.py", "*.sh", "*.R"])
    diff = bool(re.search(r"s3://[^/\s'\"]+/diffusion/|/diffusion/", text))
    checks.append(Check("publishes_under_diffusion", diff, 1.0 if diff else 0.0, axis="platform",
                        detail="chemin diffusion/ a la racine" if diff else "pas de chemin diffusion/"))
    checks.append(env_vars_used(text, ["AWS_S3_ENDPOINT", "AWS_ACCESS_KEY_ID", "AWS_SESSION_TOKEN"]))
    checks.append(no_hardcoded_secrets(text))
    return checks
