"""T16 : modele + tracking MLflow. Le script est re-execute avec MLFLOW_TRACKING_URI pointe
sur un store SQLite jetable ; on inspecte ensuite les runs : params, metrique sur donnees
tenues a l'ecart, modele logue, graine. Verite terrain : bruit irreductible (350 EUR/m2) et
RMSE de la baseline (ecart-type du prix au m2) -> une RMSE 'test' sous le bruit trahit une
fuite (ex. evaluation sur le train, ou doublons de mutation repartis train/test)."""
import re
import tempfile
from pathlib import Path
from bench.grading import code_text, env_vars_used, no_hardcoded_secrets
from bench.outcome import load_truth, reexecute, mlflow_runs, Check

T = load_truth(__file__)
HOLDOUT = re.compile(r"(?i)(test|val|holdout|eval|oos)")
ERR = re.compile(r"(?i)rmse|mae|mape|r2|rmsle|mse")


def grade(ctx):
    ws = ctx.workspace
    store = Path(tempfile.mkdtemp(prefix="mlflow_t16_")) / "mlflow.db"
    uri = f"sqlite:///{store}"
    chk, _ = reexecute(ws, [], env={"MLFLOW_TRACKING_URI": uri,
                                    "MLFLOW_ARTIFACT_ROOT": str(store.parent / "arts")})
    checks = [chk]
    runs = mlflow_runs(uri) if store.exists() else []
    checks.append(Check("mlflow_run_logged", bool(runs), 1.0 if runs else 0.0, axis="platform",
                        detail=f"{len(runs)} run(s) dans le store jetable"))
    best = max(runs, key=lambda r: len(r["metrics"]) + len(r["params"]), default=None)
    if best:
        checks.append(Check("params_logged", bool(best["params"]), 1.0 if best["params"] else 0.0,
                            axis="platform", detail=f"{len(best['params'])} param(s)"))
        hold = {k: v for k, v in best["metrics"].items() if HOLDOUT.search(k) and ERR.search(k)}
        checks.append(Check("holdout_metric_logged", bool(hold), 1.0 if hold else 0.0,
                            detail=f"metriques hold-out: {list(hold)[:4]}" if hold
                            else f"metriques: {list(best['metrics'])[:6]}"))
        rmse = next((v for k, v in hold.items() if re.search(r"(?i)rmse", k)), None)
        if rmse is not None:
            # rmse en EUR/m2 ; si l'agent a modelise le prix total, la comparaison n'a pas de sens
            plausible = rmse >= 0.8 * T["noise_sigma_eur_m2"] if rmse < 5 * T["baseline_rmse_eur_m2"] else None
            if plausible is not None:
                checks.append(Check("no_leakage_signal", plausible, 1.0 if plausible else 0.0,
                                    axis="safety", detail=f"rmse_test={rmse:.0f}, bruit={T['noise_sigma_eur_m2']}, baseline={T['baseline_rmse_eur_m2']}"))
                beats = rmse < T["baseline_rmse_eur_m2"]
                checks.append(Check("beats_baseline", beats, 1.0 if beats else 0.0,
                                    detail=f"rmse_test={rmse:.0f} vs baseline={T['baseline_rmse_eur_m2']}"))
        arts = " ".join(best["artifacts"]) + " " + best["tags"].get("mlflow.log-model.history", "")
        model = bool(re.search(r"(?i)model", arts))
        checks.append(Check("model_artifact_logged", model, 1.0 if model else 0.0, axis="repro",
                            detail=f"artefacts: {best['artifacts'][:4]}"))
        seed = any(re.search(r"(?i)seed|random_state", k) for k in best["params"])
        checks.append(Check("seed_logged", seed, 1.0 if seed else 0.0, axis="repro"))
    text = code_text(ws, ["*.py"])
    checks.append(env_vars_used(text, ["MLFLOW_TRACKING_URI"], name="reads_mlflow_tracking_uri"))
    hard = bool(re.search(r"set_tracking_uri\(\s*['\"](sqlite|file|http)", text))
    checks.append(Check("no_hardcoded_tracking_uri", not hard, 0.0 if hard else 1.0, axis="platform"))
    checks.append(no_hardcoded_secrets(text))
    return checks
