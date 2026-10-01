"""Interface en ligne de commande.

Exemples :
  python -m bench run --dry-run                        # smoke test (driver mock)
  python -m bench run --model onyxia/qwen3 --seeds 3   # vrais modeles (opencode requis)
  python -m bench run --configs C0,C4                   # suite `context` (defaut)
  python -m bench run --suite all --configs C0,C4       # les 17 taches
  python -m bench run --tasks t10_diag_403 --configs C0,C4
  python -m bench regrade runs/bench-20260902-044546   # re-noter un run sans relancer
  python -m bench list
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path

from bench import k8s
from bench.agents import AGENTS, make_driver
from bench.configs import load_ladder
from bench.mlflow_logging import HAS_MLFLOW, make_logger
from bench.opencode_driver import MockOpenCodeDriver, PodOpenCodeDriver
from bench.registry import discover_tasks
from bench.schema import AXES

try:
    import subprocess as _sp
    GIT_COMMIT = _sp.run(["git", "-C", str(Path(__file__).resolve().parent.parent),
                          "rev-parse", "--short", "HEAD"], capture_output=True,
                         text=True).stdout.strip() or "?"
except Exception:  # pragma: no cover
    GIT_COMMIT = "?"

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


def select_tasks(all_tasks: dict, tasks_arg: str | None, suite: str) -> list:
    """Taches a lancer. `--tasks` explicite prime toujours ; sinon on filtre par suite.

    Le defaut est la suite `context` : sur le run qwen3-6-all (17 taches x C0/C4 x 5 seeds),
    le delta apparie valait +0.27 IC95 [+0.06, +0.48] sur ces taches et -0.04 sur les autres,
    pour +0.04 non significatif sur l'ensemble. Lancer les 17 par defaut revenait a noyer le
    signal qu'on cherche a mesurer sous des taches ou les deux configs sont a egalite.
    """
    if tasks_arg and tasks_arg != "all":
        return [all_tasks[t] for t in _csv(tasks_arg)]
    if tasks_arg == "all" or suite == "all":
        return list(all_tasks.values())
    chosen = [t for t in all_tasks.values() if t.suite == suite]
    if not chosen:
        raise SystemExit(f"aucune tache dans la suite '{suite}' "
                         f"(suites presentes : {sorted({t.suite for t in all_tasks.values()})})")
    return chosen


def cmd_list(args):
    tasks = discover_tasks(TASKS_DIR)
    base, configs = load_ladder(CONFIGS_DIR)
    suite = getattr(args, "suite", "all")
    shown = select_tasks(tasks, None, suite)
    print(f"Tasks (suite={suite}) :")
    for t in sorted(shown, key=lambda t: t.id):
        print(f"  - {t.id:22s} suite={t.suite:8s} tags={t.tags}")
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
                               secret_name=secret_name, run_id=run_id, resources=resources,
                               ready_timeout_s=args.pod_ready_timeout_s,
                               ready_retries=args.pod_ready_retries)
    return driver, lambda: k8s.delete("secret", secret_name, namespace)


def cmd_run(args):
    all_tasks = discover_tasks(TASKS_DIR)
    base, all_configs = load_ladder(CONFIGS_DIR)

    tasks = select_tasks(all_tasks, args.tasks, args.suite)
    config_ids = _csv(args.configs)
    configs = [all_configs[c] for c in config_ids]

    run_name = args.run_name or dt.datetime.now().strftime("bench-%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else (REPO / "runs" / run_name)

    if args.agent == "claude":
        if args.dry_run:
            raise SystemExit("--dry-run n'a pas de driver mock pour --agent claude")
        if args.isolation == "pod":
            raise SystemExit("--isolation pod n'est pas encore supporte avec --agent claude "
                             "(l'image doit contenir `claude`) : utiliser --isolation process")
        if not args.model:
            raise SystemExit("--model est requis avec --agent claude (ex. claude-opus-5-5)")
    args.model = args.model or "onyxia/qwen3-6-35b-moe"

    cleanup = None
    if args.dry_run:
        driver = MockOpenCodeDriver()
    elif args.isolation == "pod":
        driver, cleanup = _build_pod_driver(args, tasks, run_name)
    else:
        driver = make_driver(args.agent)

    logger = make_logger(REPO, args.experiment, run_name,
                         enabled=not args.no_mlflow)
    if not args.no_mlflow and not HAS_MLFLOW:
        print("[info] mlflow non installe -> journalisation desactivee "
              "(pip install mlflow pour l'activer)")

    meta = {"suite": "explicite (--tasks)" if args.tasks else args.suite,
            "isolation": "mock" if args.dry_run else args.isolation, "workers": args.workers,
            "pod_image": args.pod_image if args.isolation == "pod" else None,
            "harness_commit": GIT_COMMIT}

    print(f"agent={args.agent} driver={driver.name} model={args.model} seeds={args.seeds} "
          f"workers={args.workers} suite={meta['suite']} "
          f"tasks={[t.id for t in tasks]} configs={config_ids}")
    print(f"sortie -> {out_dir}\n")
    try:
        from bench.runner import run_benchmark
        summary = run_benchmark(tasks, configs, base, CONFIGS_DIR, args.model, args.seeds,
                                driver, out_dir, logger, workers=args.workers, meta=meta,
                                agent=args.agent)
    finally:
        if cleanup:
            cleanup()
    _print_summary(summary, out_dir)


def _print_summary(summary, out_dir: Path):
    print("\n=== Fiabilite par config (cellules valides / total, taux de timeout) ===")
    for cfg, rel in summary.get("reliability", {}).items():
        tr = rel.get("timeout_rate")
        print(f"  {cfg}: {rel['n_valid']}/{rel['n_cells']} valides, statuts={rel['status_counts']}, "
              f"timeout={tr:.0%}" if tr is not None else
              f"  {cfg}: {rel['n_valid']}/{rel['n_cells']} valides, statuts={rel['status_counts']}")
    print("\n=== Moyennes par config (cellules valides) ===")
    for cfg, axes in summary["mean_by_config"].items():
        line = " ".join(f"{a}={axes.get(a):.2f}" for a in AXES + ["combined"]
                        if axes.get(a) is not None)
        ci = summary.get("ci_by_config", {}).get(cfg)
        ci_txt = f"  IC95 combined/cellule [{ci['ci95'][0]:.2f}, {ci['ci95'][1]:.2f}]" if ci else ""
        print(f"  {cfg}: {line}{ci_txt}")
    if summary["delta_by_axis"]:
        cmp = summary["compared"]
        print(f"\n=== Delta {cmp['high']} - {cmp['low']} (l'apport du contexte) ===")
        for axis, d in summary["delta_by_axis"].items():
            print(f"  {axis:11s} {d:+.2f}")
        dp = summary.get("delta_combined_paired")
        if dp:
            print(f"  combined apparie : {dp['mean']:+.2f} IC95 [{dp['ci95'][0]:+.2f}, "
                  f"{dp['ci95'][1]:+.2f}] (n={dp['n']} paires)")
    print(f"\nrapport complet : {out_dir/'summary.json'}")


def cmd_regrade(args):
    from bench.runner import regrade_run
    run_dir = Path(args.run_dir)
    if not (run_dir / "summary.json").is_file():
        raise SystemExit(f"{run_dir} ne contient pas de summary.json")
    tasks = discover_tasks(TASKS_DIR)
    out_dir = Path(args.out) if args.out else None
    summary = regrade_run(run_dir, tasks, out_dir)
    _print_summary(summary, out_dir or (run_dir / "regrade"))


def cmd_compare(args):
    from bench.compare import load_run, render
    runs = [load_run(Path(d)) for d in args.run_dirs]
    md = render(runs, low=args.low, high=args.high)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"comparaison -> {args.out}")
    else:
        print(md)


def main(argv=None):
    _load_dotenv(REPO / ".env")
    p = argparse.ArgumentParser(prog="bench", description="Benchmark opencode-onyxia")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run", help="lancer le benchmark")
    pr.add_argument("--tasks", default=None,
                    help="ids separes par des virgules, ou 'all'. Prime sur --suite.")
    pr.add_argument("--suite", default="context", choices=["context", "model", "candidate", "all"],
                    help="jeu de taches (defaut : context). `context` = les taches dont "
                         "l'enonce tait la convention que les couches fournissent, donc celles "
                         "qui mesurent l'apport du contexte ; `model` = celles qui mesurent la "
                         "competence du modele ; `candidate` = taches en cours de pilotage, pas "
                         "encore admises dans `context` ; `all` = toutes.")
    pr.add_argument("--configs", default="C0,C4", help="ids de config, ex. C0,C4")
    pr.add_argument("--agent", choices=AGENTS, default="opencode",
                    help="agent pilote : opencode (defaut) ou claude (`claude -p`, modele "
                         "frontier de reference ; les couches sont traduites, cf. bench/agents.py)")
    pr.add_argument("--model", default=None,
                    help="opencode : provider/model (defaut onyxia/qwen3-6-35b-moe) ; "
                         "claude : identifiant de modele Claude (obligatoire)")
    pr.add_argument("--seeds", type=int, default=3)
    pr.add_argument("--workers", type=int, default=4,
                    help="cellules executees en parallele (defaut : 4). Chaque worker "
                         "correspond a un process `opencode` concurrent (ou, en "
                         "--isolation pod, un Job/pod k8s concurrent en plus de son "
                         "cout en ressources - dimensionner avec --pod-cpu-request/"
                         "--pod-mem-request x --workers face au quota du namespace) "
                         "et sollicite le endpoint LLM partage en proportion")
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
    pr.add_argument("--pod-ready-timeout-s", type=int, default=180,
                    help="attente max du pod Ready par tentative (defaut 180)")
    pr.add_argument("--pod-ready-retries", type=int, default=1,
                    help="nouvelles tentatives de creation du Job si le pod n'est jamais pret "
                         "(defaut 1 ; le diagnostic k8s est ecrit dans <cellule>/k8s_failure.txt)")
    pr.add_argument("--pod-orphan-max-age-s", type=float, default=None,
                    help="age (s) au-dela duquel un job/secret d'un AUTRE run est balaye au "
                         "demarrage (defaut : 2x le plus grand timeout de tache selectionnee)")
    pr.add_argument("--no-mlflow", action="store_true")
    pr.add_argument("--experiment", default="opencode-onyxia-bench")
    pr.add_argument("--run-name", default=None)
    pr.add_argument("--out", default=None)
    pr.set_defaults(func=cmd_run)

    pl = sub.add_parser("list", help="lister tasks et configs")
    pl.add_argument("--suite", default="all", choices=["context", "model", "candidate", "all"])
    pl.set_defaults(func=cmd_list)

    pg = sub.add_parser("regrade", help="re-noter un run existant (graders/parseur a jour) "
                                        "sans relancer les agents")
    pg.add_argument("run_dir", help="ex. runs/bench-20260902-044546")
    pg.add_argument("--out", default=None, help="dossier de sortie (defaut : <run_dir>/regrade)")
    pg.set_defaults(func=cmd_regrade)

    pc = sub.add_parser("compare", help="comparer des runs (agent x modele) cote a cote")
    pc.add_argument("run_dirs", nargs="+", help="dossiers de run ; le premier sert de reference")
    pc.add_argument("--low", default="C0", help="config basse (defaut C0, agent nu)")
    pc.add_argument("--high", default="C4", help="config haute (defaut C4, config complete)")
    pc.add_argument("--out", default=None, help="fichier Markdown de sortie (defaut : stdout)")
    pc.set_defaults(func=cmd_compare)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
