# Lancer le benchmark avec Argo Workflows

Un `WorkflowTemplate` (`onyxia-agent-bench`) lance `python -m bench run` dans un pod
« orchestrateur » du cluster. On choisit **l'agent, le modèle, la suite de tâches, les configs, les
seeds…** à la soumission ; les résultats sont journalisés dans **MLflow**. En `isolation=pod`
(défaut) l'orchestrateur crée lui-même un Job éphémère par cellule, comme en local.

| Fichier | Rôle |
|---|---|
| `benchmark-workflowtemplate.yaml` | le workflow (paramètres, vérifications, run, bilan, nettoyage) |
| `benchmark-run.yaml` | un `Workflow` prêt à éditer qui référence le template |
| `rbac.yaml` | Role + RoleBinding : droits de l'orchestrateur, ajoutés au ServiceAccount `argo-workflows` |

## Prérequis

Le service **Argo Workflows** du catalogue Onyxia doit tourner dans votre namespace (il crée le
contrôleur, le serveur et le ServiceAccount `argo-workflows`). Sans lui un `Workflow` créé reste sans
statut, sans aucune erreur. Vérifier : `kubectl get deploy | grep argo-workflows` et
`kubectl get sa argo-workflows`.

Les workflows tournent sous le ServiceAccount `argo-workflows`, et non sous un ServiceAccount
dédié : c'est le seul qui reçoit `workflowtaskresults`, dont l'executor Argo a besoin dans chaque pod,
et un administrateur de namespace ne peut pas accorder ce droit à un autre (Kubernetes refuse
l'escalade de privilèges). `rbac.yaml` **ajoute donc nos droits (Jobs, Pods, Secrets…) à ce
ServiceAccount** : tous les workflows du namespace lancés avec lui en héritent. Les pods de cellule
tournent avec `default` et ne les ont pas.

## Installation (une fois par namespace)

```bash
NS=$(cat /var/run/secrets/kubernetes.io/serviceaccount/namespace)   # ou votre namespace Onyxia
sed "s/__NAMESPACE__/$NS/" argo/rbac.yaml | kubectl apply -n "$NS" -f -
kubectl apply -n "$NS" -f argo/benchmark-workflowtemplate.yaml
# puis créer le Secret (section suivante)
```

## Le Secret du namespace

Nom par défaut : **`onyxia-agent-bench`** (paramètre `secret_name`). Il est injecté en variables
d'environnement dans le pod orchestrateur (`envFrom`, optionnel : seules les clés présentes existent).
Le script vérifie les clés dont l'agent choisi a besoin et **s'arrête avant de lancer quoi que ce
soit** si elles manquent.

| Clé | Quand | Contenu |
|---|---|---|
| `MLFLOW_TRACKING_URI` | **toujours** (sauf `mlflow=false`) | URL de votre service MLflow (`https://user-<namespace>-<id>.user.lab.sspcloud.fr`). Sans elle le harnais écrirait dans un SQLite local au pod, perdu avec lui : le workflow refuse de démarrer. |
| `MLFLOW_TRACKING_USERNAME`, `MLFLOW_TRACKING_PASSWORD` | si le serveur MLflow demande une authentification | identifiants du service MLflow |
| `OPENCODE_ONYXIA_BASE_URL`, `OPENCODE_ONYXIA_API_KEY` | `agent=opencode` | endpoint OpenAI-compatible des modèles auto-hébergés et sa clé |
| `CLAUDE_CODE_OAUTH_TOKEN` | `agent=claude` | jeton généré par `claude setup-token` (identifiant d'abonnement : en prendre un dédié et révocable) |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_S3_ENDPOINT`, `MLFLOW_S3_ENDPOINT_URL` | seulement si MLflow stocke ses artefacts sur S3 **sans les proxifier** (le client les envoie alors lui-même) | jeton S3 de votre service interactif ; **expire au bout de 7 jours** : le recréer avant un run, sinon les artefacts (`ws.zip` par cellule, `summary/`) échouent avec un avertissement et le run continue sans eux |

Création (les valeurs passent par un fichier plutôt que par la ligne de commande, pour qu'elles ne
restent pas dans l'historique du shell ; le supprimer ensuite) :

```bash
umask 077
cat > /tmp/bench-secret.env <<'ENV'
MLFLOW_TRACKING_URI=https://user-<namespace>-<id>.user.lab.sspcloud.fr
MLFLOW_TRACKING_USERNAME=<utilisateur du service MLflow, si authentification>
MLFLOW_TRACKING_PASSWORD=<mot de passe du service MLflow, si authentification>
OPENCODE_ONYXIA_BASE_URL=<url de l'endpoint des modèles>
OPENCODE_ONYXIA_API_KEY=<clé>
CLAUDE_CODE_OAUTH_TOKEN=<jeton de `claude setup-token`>
ENV
kubectl create secret generic onyxia-agent-bench --from-env-file=/tmp/bench-secret.env \
  --dry-run=client -o yaml | kubectl apply -f -          # idempotent : crée ou met à jour
shred -u /tmp/bench-secret.env 2>/dev/null || rm -f /tmp/bench-secret.env
```

Ne mettre que les clés utiles : un run `claude` n'a pas besoin des clés OpenCode, et inversement.

**Ce que voit chaque pod.** L'orchestrateur voit tout le Secret. En `isolation=pod`, une cellule ne
reçoit que la clé de **son** agent (via un Secret par run, supprimé en fin de run) : ni MLflow, ni
S3, ni la clé de l'autre agent. En `isolation=process`, l'agent tourne *dans* l'orchestrateur et peut
lire tout l'environnement, Secret compris : à réserver aux essais. Le ServiceAccount de
`rbac.yaml` peut lire tous les Secrets du namespace (le RBAC ne sait pas filtrer par étiquette) ; il
n'est porté que par l'orchestrateur, jamais par les pods de cellule.

## Lancer un run

```bash
# sans la CLI argo : éditer les paramètres de benchmark-run.yaml, puis (create, PAS apply)
kubectl create -f argo/benchmark-run.yaml

# avec la CLI argo : les paramètres se passent par -p
argo submit --from workflowtemplate/onyxia-agent-bench --watch \
  -p agent=claude -p model=claude-opus-5-5 -p suite=context -p seeds=5 -p experiment=ntts2027
argo submit --from workflowtemplate/onyxia-agent-bench \
  -p agent=opencode -p model=onyxia/qwen3-8-27b -p suite=candidate -p seeds=3
# essai rapide d'une seule tâche
argo submit --from workflowtemplate/onyxia-agent-bench \
  -p tasks=t10_diag_403 -p configs=C0 -p seeds=1 -p mlflow=false
```

`argo submit` lance des conteneurs sur le cluster et, avec un modèle frontier, **dépense de
l'argent** : confirmer avant de soumettre.

| Paramètre | Défaut | Rôle |
|---|---|---|
| `agent` | `opencode` | `opencode` (modèle auto-hébergé) ou `claude` (`claude -p`) |
| `model` | *(vide)* | opencode : `provider/model` (vide = défaut du harnais). claude : **obligatoire**, identifiant **complet** : `claude-opus-5-5`, pas `opus-5.5` (un alias `opus`, `sonnet`, `haiku` marche aussi) |
| `suite` | `context` | `context`, `model`, `candidate` ou `all` |
| `tasks` | *(vide)* | ids séparés par des virgules ; non vide, remplace `suite` |
| `configs` | `C0,C4` | configs comparées (C0 = agent nu, C4 = config complète) |
| `seeds`, `workers` | `5`, `4` | répétitions ; cellules en parallèle (un Job chacune en isolation pod : à dimensionner face au quota du namespace) |
| `isolation` | `pod` | `pod` (strict, nécessite `rbac.yaml`) ou `process` |
| `pod_image` | image GHCR du harnais | image des cellules ; doit contenir `tar` et le binaire de l'agent |
| `pod_claude_version` | `2.1.286` | version de Claude Code si l'image n'en a pas |
| `experiment` | `opencode-onyxia-bench` | expérience MLflow |
| `run_name` | *(nom du workflow)* | nom du run MLflow (lettres, chiffres, `. _ -`) |
| `mlflow` | `true` | `false` : pas de journalisation (essais ; rien ne survit au pod hormis les logs) |
| `repo_url`, `repo_ref` | ce dépôt, `main` | harnais à lancer : **épingler un sha** pour un run dont on cite les chiffres |
| `image` | image GHCR du harnais | image de l'orchestrateur (git, kubectl, python + mlflow) |
| `secret_name` | `onyxia-agent-bench` | Secret des identifiants |
| `cpu`, `memory`, `scratch_size` | `2`, `4Gi`, `20Gi` | ressources de l'orchestrateur (à augmenter en `process`) ; disque temporaire pour les workspaces (appliqués par `podSpecPatch` : Argo refuse un paramètre dans une quantité) |

Le ServiceAccount (`argo-workflows`) est fixé dans le template ; un autre nom (autre installation
d'Argo) se change dans `rbac.yaml`, `benchmark-workflowtemplate.yaml` et `benchmark-run.yaml`.

## Résultats

- **MLflow** : un run parent par invocation (paramètres : agent, modèle, suite, configs, seeds,
  commit du harnais, image…) et un run enfant par cellule (scores par axe, tokens, coût, durées,
  `ws.zip`). Le parent porte `summary/summary.json` et `summary/report.md`.
- **Logs** du workflow : le `report.md` complet est affiché en fin de run
  (`kubectl logs <nom-du-workflow> -c main`, ou l'interface Argo).
- **Paramètre de sortie** `result` (visible par `argo get`) : cellules valides et delta apparié C4−C0
  (ou la raison d'un arrêt précoce).
- Le workflow **échoue** si aucune cellule valide n'a tourné (modèle injoignable, pods jamais prêts…) :
  le harnais lui-même sort en 0 dans ce cas.
- **Modèle ou clé refusés** : avant la moindre cellule, `bench run` fait un appel minuscule avec l'agent et le
  modèle choisis (pré-contrôle) ; en cas d'échec le workflow s'arrête en quelques secondes et le paramètre de
  sortie `result` contient le message de l'agent (`[preflight] ECHEC : … Failed to authenticate …`). Si un
  jeton expire ou qu'un quota est atteint en cours de run, le **disjoncteur** arrête le run après 10 cellules
  perdues d'affilée sans token (`--max-consecutive-failures`), le rapport s'ouvre sur « RUN INTERROMPU » et le
  workflow échoue avec la raison dans `result`. Les messages de l'agent sont aussi dans le rapport
  (« Messages d'erreur les plus fréquents ») et par cellule dans MLflow.
- Comparer deux runs : télécharger leurs `summary/` depuis MLflow dans `runs/<nom>/`, puis
  `python -m bench compare runs/<run-opencode> runs/<run-claude>`.

## Arrêt et nettoyage

`argo stop <workflow>` / `kubectl delete workflow <nom>` : l'étape `onExit` supprime les Jobs de cellule
et le Secret du run (`-l app=onyxia-agent-bench,bench/run-id=<run>`), qui contient la clé de l'agent.
Les Jobs ont de toute façon `activeDeadlineSeconds` et `ttlSecondsAfterFinished`. Les workflows
terminés sont supprimés au bout de 7 jours (les résultats sont dans MLflow) ; le workflow entier est
plafonné à 48 h (`activeDeadlineSeconds`).

## Ce qui est vérifié, et ce qui ne l'est pas

**Vérifié sur un cluster réel** (Argo Workflows 3.6.10 du catalogue Onyxia, sans modèle ni
dépense, avec un Secret factice) : un workflow soumis tourne jusqu'au bout. Le harnais est cloné au
sha demandé, l'orchestrateur sous `argo-workflows` crée, exécute puis supprime un Job de cellule en
isolation pod (les droits de `rbac.yaml` suffisent), la cellule échoue faute de modèle joignable et
le workflow **échoue** avec `0/1 cellules valides` en paramètre de sortie, l'étape `onExit` s'exécute
et rien ne reste dans le namespace. Un run `claude` sans jeton et un run sans `MLFLOW_TRACKING_URI`
s'arrêtent en une dizaine de secondes avec un message lisible, avant toute cellule. Hors cluster,
`tests/test_argo_workflow.py` exécute le script de l'orchestrateur avec `git` et `python` simulés
(garde-fous, arguments, valeurs hostiles inertes, échec si aucune cellule valide) et vérifie la
cohérence des manifestes avec le harnais.

**Non vérifié** : l'écriture dans un vrai serveur MLflow (aucun n'était disponible ; le logger lui-même
est testé) et une cellule facturée avec un vrai modèle (OpenCode ou Claude). Le premier vrai run le
confirmera : regarder l'expérience dans MLflow.

**Deux erreurs que seul le vrai cluster a montrées**, utiles si vous modifiez le template : un
paramètre `{{...}}` ne peut pas figurer dans une quantité (`resources`, `sizeLimit`), Argo la valide
avant de substituer ; et un Role contenant `workflowtaskresults` ne peut pas être créé par un
administrateur de namespace.
