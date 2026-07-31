"""Interface en ligne de commande.

Exemples :
  python -m bench run --dry-run                        # smoke test (driver mock)
  python -m bench run --model onyxia/qwen3 --seeds 3   # vrais modeles (opencode requis)
  python -m bench run --tasks t10_diag_403 --configs C0,C4
  python -m bench list
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path

from bench import k8s
from bench.configs import load_ladder
from bench.mlflow_logging import HAS_MLFLOW, make_logger
from bench.opencode_driver import MockOpenCodeDriver, PodOpenCodeDriver, RealOpenCodeDriver
from bench.registry import discover_tasks
from bench.schema import AXES

REPO = Path(__file__).resolve().parent.parent
CONFIGS_DIR = REPO / "configs"
TASKS_DIR = REPO / "tasks"
_SA_NAMESPACE_FILE = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")


def _detect_namespace(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    if _SA_NAMESPACE_FILE.is_file():
        return _SA_NAMESPACE_FILE.read_text(encoding="utf-8").strip()
    return os.environ.get("KUBERNETES_NAMESPACE")


def _load_dotenv(path: Path) -> None:
    """Charge un .env (KEY=VALUE) dans os.environ sans ecraser les variables deja definies."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def _csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def cmd_list(_args):
    tasks = discover_tasks(TASKS_DIR)
    base, configs = load_ladder(CONFIGS_DIR)
    print("Tasks :")
    for t in tasks.values():
        print(f"  - {t.id:18s} tags={t.tags}")
    print(f"\nConfigs (base={base}) :")
    for c in configs.values():
        print(f"  - {c.id:3s} layers={c.layers}")


def _build_pod_driver(args, tasks, run_id: str):
    """Renvoie (driver, cleanup) : `cleanup()` supprime le Secret cree pour ce run."""
    if not args.pod_image:
        raise SystemExit("--pod-image est requis avec --isolation pod "
                         "(ex. inseefrlab/onyxia-vscode-r-python-julia:<tag>)")
    namespace = _detect_namespace(args.pod_namespace)
    if not namespace:
        raise SystemExit("namespace k8s introuvable (auto-detection echouee) : "
                         "passe --pod-namespace explicitement")
    base_url = os.environ.get("OPENCODE_ONYXIA_BASE_URL")
    api_key = os.environ.get("OPENCODE_ONYXIA_API_KEY")
    if not base_url or not api_key:
        raise SystemExit("OPENCODE_ONYXIA_BASE_URL/OPENCODE_ONYXIA_API_KEY manquants "
                         "(.env) : requis pour creer le Secret k8s des cellules")

    max_age = args.pod_orphan_max_age_s or (2 * max((t.timeout_s for t in tasks), default=900))
    print(f"[pod] menage des jobs/secrets abandonnes (namespace={namespace}, "
          f"age > {max_age:.0f}s)...")
    k8s.sweep_orphans(namespace, run_id, max_age)

    secret_name = f"bench-creds-{k8s.sanitize_label(run_id)}"
    secret_manifest = k8s.build_secret_manifest(
        name=secret_name, namespace=namespace, run_id=run_id,
        base_url=base_url, api_key=api_key)
    k8s.kubectl_apply(secret_manifest)

    resources = {
        "requests": {"cpu": args.pod_cpu_request, "memory": args.pod_mem_request},
        "limits": {"cpu": args.pod_cpu_limit, "memory": args.pod_mem_limit},
    }
    driver = PodOpenCodeDriver(image=args.pod_image, namespace=namespace,
                               secret_name=secret_name, run_id=run_id, resources=resources)
    return driver, lambda: k8s.delete("secret", secret_name, namespace)


def cmd_run(args):
    all_tasks = discover_tasks(TASKS_DIR)
    base, all_configs = load_ladder(CONFIGS_DIR)

    task_ids = _csv(args.tasks) if args.tasks != "all" else list(all_tasks)
    tasks = [all_tasks[t] for t in task_ids]
    config_ids = _csv(args.configs)
    configs = [all_configs[c] for c in config_ids]

    run_name = args.run_name or dt.datetime.now().strftime("bench-%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else (REPO / "runs" / run_name)

    cleanup = None
    if args.dry_run:
        driver = MockOpenCodeDriver()
    elif args.isolation == "pod":
        driver, cleanup = _build_pod_driver(args, tasks, run_name)
    else:
        driver = RealOpenCodeDriver()

    logger = make_logger(REPO, args.experiment, run_name,
                         enabled=not args.no_mlflow)
    if not args.no_mlflow and not HAS_MLFLOW:
        print("[info] mlflow non installe -> journalisation desactivee "
              "(pip install mlflow pour l'activer)")

    print(f"driver={driver.name} model={args.model} seeds={args.seeds} "
          f"tasks={task_ids} configs={config_ids}")
    print(f"sortie -> {out_dir}\n")

    try:
        from bench.runner import run_benchmark
        summary = run_benchmark(tasks, configs, base, CONFIGS_DIR, args.model, args.seeds,
                                driver, out_dir, logger)
    finally:
        if cleanup:
            cleanup()

    print("\n=== Moyennes par config ===")
    for cfg, axes in summary["mean_by_config"].items():
        line = " ".join(f"{a}={axes.get(a):.2f}" for a in AXES + ["combined"] if a in axes)
        print(f"  {cfg}: {line}")
    if summary["delta_by_axis"]:
        cmp = summary["compared"]
        print(f"\n=== Delta {cmp['high']} - {cmp['low']} (l'apport du contexte) ===")
        for axis, d in summary["delta_by_axis"].items():
            print(f"  {axis:11s} {d:+.2f}")
    print(f"\nrapport complet : {out_dir/'summary.json'}")


def main(argv=None):
    _load_dotenv(REPO / ".env")
    p = argparse.ArgumentParser(prog="bench", description="Benchmark opencode-onyxia")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run", help="lancer le benchmark")
    pr.add_argument("--tasks", default="all", help="ids separes par des virgules, ou 'all'")
    pr.add_argument("--configs", default="C0,C4", help="ids de config, ex. C0,C4")
    pr.add_argument("--model", default="onyxia/qwen3-6-35b-moe", help="provider/model pour opencode")
    pr.add_argument("--seeds", type=int, default=3)
    pr.add_argument("--dry-run", action="store_true", help="driver mock (sans vrai modele)")
    pr.add_argument("--isolation", choices=["process", "pod"], default="process",
                    help="process = sous-processus local (defaut) ; "
                         "pod = Job Kubernetes ephemere par cellule (isolation stricte)")
    pr.add_argument("--pod-image", default=None,
                    help="image conteneur pour --isolation pod, ex. "
                         "inseefrlab/onyxia-vscode-r-python-julia:<tag>")
    pr.add_argument("--pod-namespace", default=None,
                    help="namespace k8s (defaut : auto-detecte depuis le pod courant)")
    pr.add_argument("--pod-cpu-request", default="500m")
    pr.add_argument("--pod-mem-request", default="1Gi")
    pr.add_argument("--pod-cpu-limit", default="2")
    pr.add_argument("--pod-mem-limit", default="4Gi")
    pr.add_argument("--pod-orphan-max-age-s", type=float, default=None,
                    help="age (s) au-dela duquel un job/secret d'un AUTRE run est balaye au "
                         "demarrage (defaut : 2x le plus grand timeout de tache selectionnee)")
    pr.add_argument("--no-mlflow", action="store_true")
    pr.add_argument("--experiment", default="opencode-onyxia-bench")
    pr.add_argument("--run-name", default=None)
    pr.add_argument("--out", default=None)
    pr.set_defaults(func=cmd_run)

    pl = sub.add_parser("list", help="lister tasks et configs")
    pl.set_defaults(func=cmd_list)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
