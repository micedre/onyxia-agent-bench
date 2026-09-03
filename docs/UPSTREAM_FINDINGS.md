# Retours pour `inseefrlab/opencode-onyxia` issus du benchmark

Source : `onyxia-agent-bench`, run `bench-20260902-044546` (modele `onyxia/qwen3-8-27b`, 11 taches
× C0..C4 × 3 seeds, isolation pod, 4 workers) et les runs precedents avec `qwen3-6-35b-moe`.
Les couches C1..C4 sont une copie synchronisee de la config upstream (`configs/UPSTREAM_SYNC.md`,
commit `39d7071`). Rien ici n'a ete modifie cote couches : ce document est une liste de
propositions a discuter en issue/PR upstream, avec les mesures qui les motivent.

Contexte de lecture : le benchmark lance `opencode run --agent build --format json "<prompt>"`,
c'est-a-dire **sans utilisateur**. Plusieurs constats ci-dessous ne concernent que ce mode
non interactif (CI, jobs, evaluation) - mais c'est un mode que la config devrait supporter
proprement, ne serait-ce que pour etre mesurable.

## 1. Ce que le benchmark a mesure

Medianes par cellule (extraites des evenements `step_finish` d'opencode) :

| config | prompt initial (tok) | tours LLM | Σ tokens entree | Σ sortie + raisonnement | s / tour | appels bash rejetes |
|---|---|---|---|---|---|---|
| C0 (nu) | 7 160 | 10 | 100 k | 6.7 k | 12.4 | 5 |
| C1 (+AGENTS.md) | 9 389 | 11 | 123 k | 9.0 k | 18.5 | 9 |
| C2 (+skills) | 11 999 | 23 | 490 k | 13 k | 17.6 | 5 |
| C3 (+commands) | 11 992 | 17 | 313 k | 12.8 k | 21.3 | 4 |
| C4 (+guardrails) | 13 108 | 19 | 459 k | **30 k** | **33.7** | **91** |

Timeouts (budget 900 s) : C0 8/33, C1 11, C2 13, C3 16, **C4 24/33**. Duree moyenne par
cellule : 264 s (C0) → 781 s (C4). Avec le modele 35B MoE (plus rapide), les memes couches
donnaient C0 60-100 s → C4 200-316 s : le facteur ×3 a ×5 est stable, seul le plafond change.

Le contexte toujours charge reste modeste (~6 k tokens en C4). **Le surcout est comportemental** :
les couches demandent plus de travail (charger des skills, ecrire un plan, rejouer toutes les
verifications, appeler un reviewer, obtenir un PASS), et chaque tour supplementaire re-envoie
tout le prompt.

Sur ce run le delta C4−C0 etait negatif (−0.14). Une part importante est imputable au harnais
(timeouts et cellules jamais lancees comptees comme des zeros, notation sur l'hote sans les
dependances du projet) - corrige depuis dans le benchmark. Mais les mecanismes ci-dessous sont
bien dans la config et penalisent le mode non interactif independamment du harnais.

## 2. Permissions `ask` en mode non interactif = rejets en boucle

`opencode.json` (guardrails) :

```json
"permission": { "webfetch": "ask", "bash": { "*": "ask", "git *": "allow", ... , "rm *": "ask", "curl *": "ask" } },
"experimental": { "continue_loop_on_deny": true }
```

En `opencode run`, personne ne repond a `ask` : l'appel est rejete
(`The user rejected permission to use this specific tool call.`) et, avec `continue_loop_on_deny`,
l'agent est invite a continuer - il essaie une variante, qui est rejetee aussi. **91 rejets en C4**
contre 5 en C0, a ~34 s le tour. Commandes rejetees observees : `env`, `printenv`, `command -v`,
`for … do … done`, `timeout 300 uv add …`, `rm -f main.py` (apres `uv init`), `uvx`, et toute
commande composee dont un segment n'est pas dans la liste (`cd x && python3 y.py | sed …`).

Propositions :
- Fournir un **profil "headless"** documente (ou detecter `CI=1` / absence de TTY) :
  `"*": "deny"` avec les memes allow explicites, `webfetch: "deny"`, et
  `continue_loop_on_deny: false`. Un deny est honnete et coute un tour ; un ask sans repondant
  coute un tour *et* invite a recommencer.
- Completer la liste d'allow avec les commandes d'orientation de base : `env*`, `printenv*`,
  `command -v*`, `uvx *`, `timeout *`, `printf*`, `sed*`, `awk*`, `xargs*`, `for *`, `tee*`,
  et `rm *` limite au workspace (garder `rm -rf /*`, `rm -rf ~` en ask/deny). `env` est
  particulierement important : `AGENTS.md` liste une table de variables a lire, et la premiere
  action d'un agent qui suit le guide est `env | grep AWS_`.
- Documenter que les pipes/`&&` sont evalues dans leur ensemble : un segment non liste rejette
  toute la commande.

## 3. Le contrat de completion et la porte `@reviewer`

`prompts/shared/completion-contract.md` + `prompts/build.md` : liste d'acceptation ecrite sur
disque (`.opencode/plans/*.md`), rejouer toutes les verifications, deleguer a `@reviewer`, jusqu'a
deux tours de FAIL, puis "report to the user and ask". Observe en C4 uniquement : 12 fichiers de
plan, 7 delegations reelles au reviewer, +17 k tokens de raisonnement par cellule.

Deux problemes distincts :
- **Cout** : sur un budget de 15 min et un modele 27B, le passage plan → travail → re-verification
  → reviewer (autre modele, `steps: 40`) → eventuel second tour double le temps de la tache.
  Proposition : rendre la porte reviewer optionnelle (variable/flag), ou un seul tour, et ne
  l'exiger que pour les taches qui modifient plus de N fichiers.
- **Modele** : `reviewer` tourne sur `gemma4-26b-moe` quel que soit le modele principal. Toute
  evaluation de la config est donc en partie une evaluation de gemma4. Proposition : reviewer sur
  le meme modele par defaut, configurable.

## 4. Instructions qui supposent un utilisateur present

Une dizaine de passages : « show it to the user before you start … ask », « confirm first »
(base.md), « STOP … and ask the user » (build.md, completion-contract §5), « ask about anything not
already specified » (new-project.md), « The user moves to `build` themselves (Tab) » (plan.md).
En non interactif, l'agent soit termine son tour sur une question sans reponse, soit s'arrete.

Proposition : une regle explicite « si aucun utilisateur ne peut repondre (CI, `opencode run`),
enonce l'hypothese retenue et continue ; ne pose une question que si toute hypothese rendrait le
travail inutile ». Et retirer de `completion-contract.md` les references au fichier « ecrit par
l'agent `plan` » quand `plan` n'est pas l'agent courant : `build` cherche un plan qui n'existe
pas, en ecrit un, puis attend une confirmation qui ne viendra pas.

## 5. AGENTS.md : « load the corresponding skill … before producing code »

Cette clause s'applique aussi quand les skills ne sont pas installees (C1 dans le benchmark, mais
aussi tout projet qui adopte AGENTS.md sans `.opencode/skills/`) : recherches `glob`/`read`
infructueuses a chaque cellule (+90 s par cellule en C1 pour 2 k tokens de contexte). Proposition :
conditionner (« if a skill of that name is available ») ou deplacer la liste dans la description
des skills elles-memes.

Par ailleurs `AGENTS.md` recommande `pandera`, `tabulate`, `pointblank`, `cartiflette`, `pynsee`,
absents de l'image `inseefrlab/onyxia-vscode-r-python-julia` : chaque recommandation suivie coute
un `uv add` (reseau, temps) dans le budget de la tache. Soit les embarquer dans l'image, soit le
dire (« ajoute-les au projet avec `uv add` avant de les importer »).

## 6. Couche `command/` : inerte en `opencode run`

Les slash-commands (`/new-project`, `/verify`, …) sont invoquees par l'utilisateur ; en non
interactif le modele ne les voit jamais. Elles n'ajoutent aucun token de prompt, mais les
fichiers sont dans le workspace et l'agent les lit en s'orientant (328-444 references dans les
transcripts C3/C4). Rien a changer upstream, mais c'est utile a savoir pour toute evaluation :
C3 = C2 + bruit.

## 7. `edit: allow` de premier niveau

Le commentaire de `~/.config/opencode/opencode.jsonc` explique pourquoi `edit` n'est pas accorde
globalement (un sous-agent herite des permissions de la session et un `edit: allow` global ecrase
son `edit: deny`). Dans le benchmark, la fusion base + patch laissait passer le `edit: allow` de la
config nue - constat propre au harnais (corrige par une regle de fusion), mais il vaut la peine de
documenter upstream que toute config *projet* fusionnee avec la config *globale* peut recreer le
probleme, et que `reviewer`/`dataviz-vision` deviennent alors capables d'editer.

## 8. Divers

- `qwen3-8-27b` n'est pas declare dans la config globale (`provider.models`) ; le benchmark le
  declare dans sa base.
- Aucune `limit.context/output` ni `enable_thinking` pour les modeles autres que le 35B : le mode
  raisonnement suit le defaut du gateway (actif), ce qui alourdit chaque tour.
- Observation securite (t11) : faute de vision, un agent a reutilise `OPENCODE_ONYXIA_API_KEY`
  depuis bash pour appeler lui-meme un modele vision via `/v1/chat/completions`. Ingenieux, mais
  c'est un detournement d'identifiants fournis pour un autre usage ; une regle « les identifiants
  du provider ne servent pas a des appels API manuels » aurait sa place dans `base.md`.

## 9. Ce que le benchmark fera de son cote

- Cellules non lancees exclues des moyennes, taux de timeout et cout (tokens, tours, rejets)
  rapportes par config, intervalles de confiance.
- Notation dans l'environnement du projet de l'agent (`uv run`), plus dans le venv du harnais.
- A venir : rungs `C4-noperm` / `C4-noreview` pour separer la semantique des garde-fous de leur
  plomberie, et regle de fusion supprimant le `edit: allow` herite.
