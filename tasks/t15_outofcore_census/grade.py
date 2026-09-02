"""T15 : agregation hors memoire. Note le resultat (echantillon local via CENSUS_PATH), un
signal d'usage d'un moteur paresseux (duckdb/polars scan/pyarrow dataset + colonnes
projetees) et le pic memoire de la re-execution (metrique ; `peak_rss_mb` doit rester
tres inferieur a ce qu'exigerait le chargement d'un fichier de 1 Go)."""
import re
from bench.grading import code_text, env_vars_used, no_hardcoded_secrets
from bench.outcome import load_truth, reexecute, find, read_table, compare_keyed, to_float, Check

T = load_truth(__file__)
OUT = "indicateurs_departement.csv"
LAZY = re.compile(r"(?i)duckdb|scan_parquet|pl\.scan|pyarrow\.dataset|ds\.dataset|read_parquet\([^)]*columns=|iter_batches|LazyFrame")


def grade(ctx):
    ws = ctx.workspace
    sample = find(ws, "fd_indcvi_2020_sample.parquet")
    chk, info = reexecute(ws, [OUT], env={"CENSUS_PATH": str(sample)} if sample else None)
    checks = [chk]
    p = find(ws, OUT)
    if p:
        rows = read_table(p)
        cols = list(rows[0].keys()) if rows else []
        dc = next((c for c in cols if re.search(r"(?i)dep", c)), cols[0] if cols else None)
        ac = next((c for c in cols if re.search(r"(?i)age", c)), None)
        lc = next((c for c in cols if re.search(r"(?i)loc|tenant|part", c)), None)
        age = {str(r[dc]).strip(): to_float(r.get(ac)) for r in rows} if dc and ac else {}
        loc = {str(r[dc]).strip(): to_float(r.get(lc)) for r in rows} if dc and lc else {}
        checks.append(compare_keyed({k: v for k, v in age.items() if v is not None},
                                    T["weighted_median_age_by_dept"], name="median_age_correct",
                                    abs_tol=1.0, alt_key=lambda k: k.lstrip("0")))
        loc = {k: (v / 100 if v is not None and v > 1 else v) for k, v in loc.items()}
        checks.append(compare_keyed({k: v for k, v in loc.items() if v is not None},
                                    T["tenant_share_by_dept"], name="tenant_share_correct",
                                    abs_tol=0.005, alt_key=lambda k: k.lstrip("0")))
    else:
        checks += [Check("median_age_correct", False, 0.0, detail="sortie absente"),
                   Check("tenant_share_correct", False, 0.0, detail="sortie absente")]
    text = code_text(ws, ["*.py"])
    lazy = bool(LAZY.search(text))
    checks.append(Check("lazy_engine_used", lazy, 1.0 if lazy else 0.0, axis="platform",
                        detail="moteur paresseux/colonnes projetees" if lazy else "chargement complet probable"))
    checks.append(env_vars_used(text, ["CENSUS_PATH"], name="reads_census_path_env"))
    peak = info.get("peak_rss_mb")
    if peak is not None:
        ctx.metrics["peak_rss_mb"] = round(peak, 1)
        ok = peak < 60 * T["file_mb"]  # ~15x le fichier suffit a un chargement pandas complet
        checks.append(Check("memory_footprint", ok, 1.0 if ok else 0.0, axis="efficiency",
                            detail=f"peak_rss={peak:.0f} MB pour un fichier de {T['file_mb']} MB"))
    checks.append(no_hardcoded_secrets(text))
    return checks
