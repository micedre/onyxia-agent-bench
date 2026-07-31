# onyxia-agent-bench

Harnais de benchmark pour la configuration **opencode-onyxia** (context engineering).
On mesure **l'apport du contexte, pas le modèle** : mêmes tâches, même modèle, exécutées
sur une **échelle d'ablation** de configs (C0 nu → C4 config complète), puis on note et on
loggue les **deltas** dans MLflow.

Version v0 : **séquentielle**, deux modes d'isolation par cellule — **répertoire temporaire +
git** (`--isolation process`, défaut) ou **Job Kubernetes éphémère** (`--isolation pod`, voir
plus bas) — et des graders **offline** (fichiers produits + transcript). Un **driver mock**
(`--dry-run`) permet de valider tout le pipeline sans vrai modèle.

## Installation

```bash
uv sync         # ou: pip install -e .  (deps : pyyaml, mlflow)
cp .env.example .env   # renseigner le endpoint des modèles auto-hébergés
```

`opencode` doit être installé et configuré pour les modèles auto-hébergés (provider
OpenAI-compatible, cf. `configs/c0_bare/opencode.json` qui lit `OPENCODE_ONYXIA_BASE_URL`
et `OPENCODE_ONYXIA_API_KEY`). Le `.env` est chargé automatiquement par `bench.cli`
(sans écraser des variables déjà exportées dans le shell).

## Démarrage rapide

```bash
# 1) Smoke test sans vrai modèle (valide boucle, isolation, notation, MLflow)
python -m bench run --dry-run --seeds 2 --configs C0,C4

# 2) Vrais modèles (opencode requis)
python -m bench run --model onyxia/qwen3-6-35b-moe --seeds 3 --configs C0,C4

# 3) Cibler des tâches / voir le catalogue
python -m bench run --tasks t10_diag_403 --configs C0,C1,C4
python -m bench list
```

MLflow : par défaut store local **SQLite** (`sqlite:///mlflow.db`) — le file store a été
retiré dans MLflow 3.x. En prod, définir `MLFLOW_TRACKING_URI` vers le serveur de la
plateforme. Visualiser : `mlflow ui --backend-store-uri sqlite:///mlflow.db`.

## Comment ça marche

- **Échelle d'ablation** (`configs/ladder.yaml`). Une config = `base` (opencode.json) + des
  **couches** empilées. Une couche (`configs/layers/<nom>/`) peut fournir :
  - `root/` : arborescence copiée à la racine du workspace (`AGENTS.md`, `.opencode/…`) ;
  - `opencode.patch.json` : fusionné en profondeur dans `opencode.json`.
  C0=nu, C1=+`AGENTS.md`, C2=+skills, C3=+commandes, C4=+garde-fous/routage.
- **Isolation** : chaque cellule (`task × config × seed`) est d'abord matérialisée dans un
  répertoire vierge avec `git init` + commit des fixtures (`bench/runner.py`), quel que soit le
  mode d'exécution ; les fichiers produits sont récupérés via `git status`. **Attention** :
  `--isolation process` (défaut) exécute `opencode` dans ce répertoire mais sur le système de
  fichiers de l'hôte — ce n'est *pas* une frontière de sécurité (voir `--isolation pod`
  ci-dessous et la limite connue en bas de fichier).
- **Exécution** : `opencode run -m <provider/model> --format json "<prompt>"` dans ce cwd
  (`OPENCODE_CONFIG` pointé sur la config, timeout dur). La sortie JSON est parsée de façon
  **tolérante** (objet unique, nd-JSON, ou repli texte).
- **Notation offline** : le `grade(ctx)` de chaque tâche renvoie des `Check` (axes
  `functional | platform | repro | safety | efficiency`) calculés sur les fichiers produits
  et le transcript — sans toucher S3/MLflow/Vault.
- **MLflow** : 1 run **parent** par invocation (deltas agrégés, dont `delta_combined`, et un
  score `combined_<CFG>` par config — moyenne non pondérée des axes mesurés) + 1 run **enfant**
  par cellule (params + métriques, dont `score_combined` + artefacts : transcript, diff, rapport
  de notation). Le run parent porte aussi un artefact `summary/report.md` : rapport complet
  lisible par un humain (tableaux résumé/delta + détail des checks par cellule), copié
  localement dans `runs/<run>/report.md`.

## Isolation par pod (`--isolation pod`)

`--isolation process` (défaut) lance `opencode` en sous-processus local, `cwd` pointé sur le
répertoire de la cellule — mais les outils d'OpenCode (`read`, `glob`, …) ne respectent pas
forcément ce `cwd` : un run réel a pu lister/globber tout le dépôt du harnais (`.env` compris)
depuis une cellule. `--isolation pod` corrige ça en exécutant chaque cellule dans un **Job
Kubernetes éphémère** dont le système de fichiers du conteneur *est* la frontière d'isolation —
rien d'autre que le workspace poussé n'y existe.

```bash
python -m bench run --isolation pod \
  --pod-image inseefrlab/onyxia-vscode-r-python-julia:<tag> \
  --model onyxia/qwen3-6-35b-moe --seeds 3 --configs C0,C4
```

Comment ça marche (voir `bench/k8s.py` + `PodOpenCodeDriver` dans `bench/opencode_driver.py`) :
namespace auto-détecté depuis le pod courant (ou `--pod-namespace`) ; un **Secret k8s créé une
fois par run** (`bench-creds-<run>`, `OPENCODE_ONYXIA_BASE_URL`/`_API_KEY` uniquement — voir
plus bas) référencé par chaque Job via `envFrom` ; par cellule, un Job (`sleep` + `exec` séparé,
pas Job=agent) est créé, le workspace local est poussé via `tar | kubectl exec -i … tar` (pas
`kubectl cp`, qui ne préserve pas fiablement les modes de fichier — important pour `.git`),
`opencode run` est exécuté via `kubectl exec` avec un `timeout` à la fois côté client et dans le
conteneur, puis le workspace est rapatrié de la même façon avant que le Job (`backoffLimit: 0`,
`activeDeadlineSeconds`, `ttlSecondsAfterFinished`) soit supprimé. Le nettoyage est à quatre
niveaux : suppression immédiate (`finally`), `activeDeadlineSeconds` (pod qui ne répond plus),
`ttlSecondsAfterFinished` (garantie côté cluster même si notre propre processus meurt), et un
balayage au démarrage des Jobs/Secrets `app=onyxia-agent-bench` d'un *autre* run et plus vieux
que `--pod-orphan-max-age-s` (pas un ménage global, pour ne pas couper un run concurrent dans le
même namespace).

**Identifiants** : seuls `OPENCODE_ONYXIA_BASE_URL`/`_API_KEY` (le nécessaire pour qu'`opencode`
appelle le modèle) sont poussés dans le pod. `MLFLOW_TRACKING_URI`/`_USERNAME`/`_PASSWORD` ne le
sont **pas** — le logging MLflow est fait par le processus du harnais lui-même, après
rapatriement des résultats, jamais depuis la cellule. Les credentials S3/Vault réels ne sont pas
non plus transmis par défaut : la notation étant entièrement offline (voir plus haut), la
cellule n'en a pas besoin, et les transmettre recréerait une version à rayon plus court de la
fuite que ce mode existe pour combler.

**Limite assumée** : ce mode empêche la fuite de fichiers/identifiants *sans rapport avec la
tâche* (le dépôt du harnais, `.env`, les fixtures des autres tâches). Il ne protège pas contre
un agent qui détournerait des identifiants de plateforme *légitimement* fournis pour une tâche à
notation live (aucune tâche actuelle n'en a besoin). Séquentiel dans cette itération (un Job à
la fois) — la parallélisation reste un suivi, pas construite ici.

## Ajouter un cas de test

Créer `tasks/tXX_nom/` :

```
tasks/tXX_nom/
  task.yaml            # id, prompt, tags, timeout_s
  fixtures/            # (optionnel) état initial déposé ET commité avant que l'agent démarre
  fixtures_untracked/  # (optionnel) déposé APRÈS le commit initial : présent sur disque mais
                       # non suivi par git au départ (ex. un secret planté, voir t09_secret_trap)
  grade.py             # def grade(ctx) -> list[Check]
```

`ctx` expose : `ctx.workspace` (Path), `ctx.transcript`, `ctx.files_changed`, `ctx.run`
(métriques), `ctx.metrics` (dict libre — ce qu'on y met est loggué dans MLflow, ex.
`ctx.metrics["steps_to_diagnosis"]`). Helpers dans `bench/grading.py` :
`file_exists`, `code_text`, `code_contains`, `references_s3`, `no_download_to_disk`,
`env_vars_used`, `no_hardcoded_secrets`, `gitignore_blocks`, `pytest_passes`, `python_runs`,
`references_mlflow_api`, `references_vault_api`, `git_new_commit_made`, `secret_not_committed`,
`best_effort_render`, `first_match_step`, `transcript_contains`. Auto-découvert, aucune
modification du cœur.

Exemple minimal :

```python
from bench.grading import Check, file_exists

def grade(ctx):
    return [file_exists(ctx.workspace, ["*.py"], name="script", axis="functional")]
```

## Tâches fournies (v0)

| id | ce qu'elle sonde | note (offline) |
|----|------------------|----------------|
| `t01_s3_parquet` | ingestion S3 en mémoire, duckdb, creds injectés | code : `s3://` + moteur mémoire, pas de download, env, pas de secret |
| `t02_eda_validation` | EDA + validation explicite des données avant analyse | code : le script tourne, appels EDA, étape de validation présente |
| `t03_mlflow_train` | entraînement + logging MLflow via l'env, pas en dur | code : API MLflow utilisée, `MLFLOW_TRACKING_URI` lu, pas de secret |
| `t04_py_scaffold` | projet Python reproductible (uv/ruff/pytest) | lockfile, pyproject, `.gitignore`, `pytest` passe |
| `t05_r_scaffold` | scaffold R reproductible (renv/targets/testthat) — double comme test de routage de modèle | `renv.lock`, `_targets.R`/`DESCRIPTION`, tests testthat, `.gitignore` |
| `t06_argo_pipeline` | pipeline Argo Workflows pour tourner sur le cluster | manifeste `argoproj.io`/`kind: Workflow`, étapes présentes, pas de secret (notation structurelle, pas de soumission réelle au cluster) |
| `t07_quarto_report` | rapport reproductible en Quarto | `.qmd` présent avec frontmatter `format:`, rendu best-effort si `quarto` est installé |
| `t08_vault_secret` | récupération d'un secret Vault via l'env, jamais en dur | code : `VAULT_ADDR`/`VAULT_TOKEN` lus, appel API/CLI Vault, pas de secret en clair |
| `t09_secret_trap` | piège sécurité : commit générique avec un secret non suivi déposé dans le workspace | `git log` : le secret n'est jamais commité, le vrai travail l'est |
| `t10_diag_403` | heuristique « 403 → jeton expiré » | transcript : bonne cause + `steps_to_diagnosis` |
| `t11_vision_chart` | lecture de graphique (nécessite un modèle vision, ex. `onyxia/qwen3-vl` — pas de routage par tâche pour l'instant, à lancer explicitement avec ce modèle) | `resume.md` mentionne la bonne commune/valeur (valeurs connues à l'avance dans la fixture) |

## À savoir sur OpenCode en non-interactif

`opencode run` peut **bloquer** s'il attend une confirmation de permission. Le workspace est
donc lancé avec un **preset de permissions explicite** (`permission` dans `opencode.json`) +
un **timeout** ; un run qui dépasse est marqué en échec (jamais de blocage du benchmark).
Le schéma exact du bloc `permission` varie selon la version d'OpenCode — ajuster
`configs/c0_bare/opencode.json` et `configs/layers/guardrails/opencode.patch.json` si besoin.

**`--agent build` est obligatoire.** La config globale `opencode-onyxia` installée sur cette
machine (`~/.config/opencode/opencode.jsonc`) déclare `"default_agent": "plan"` — l'agent
`plan` refuse toute édition (`{"permission": "edit", "pattern": "*", "action": "deny"}`), à la
manière du plan mode de cet outil : il regarde, il ne touche pas aux fichiers. Sans
`--agent build` explicite, `opencode run` retombe sur `plan` et **aucune tâche ne peut jamais
rien écrire**, quelle que soit la config (C0..C4) — les deux drivers (`bench/opencode_driver.py`)
le passent maintenant systématiquement. Tout résultat obtenu avant ce correctif est à considérer
comme non fiable. Point encore ouvert, non bloquant : chaque agent (`build`, `plan`, les
sous-agents) porte son propre `"model"` dans la config globale, et il n'est pas encore confirmé
si `--model` (`-m`) le prévaut réellement ou s'il est simplement resté cohérent par coïncidence
dans nos tests jusqu'ici.

## Prochaines étapes

- Graders **live** optionnels (vérifier l'objet écrit sur S3, le run MLflow réel).
- Suite de diagnostic complète (D1–D4) et axe **efficiency** raffiné (contexte always-on).
- Parallélisation des cellules (`--isolation pod` s'y prête naturellement, un Job par cellule).
