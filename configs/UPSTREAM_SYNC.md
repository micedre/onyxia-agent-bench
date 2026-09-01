# Sync status vs. `inseefrlab/opencode-onyxia`

This benchmark's `configs/layers/{agents_md,skills,commands,guardrails}/root/` content is
sourced from the upstream `opencode-onyxia` repository, so that the ablation ladder tests
the *real* config data-scientists actually run, not a hand-maintained approximation of it.

- **Upstream repo**: <https://github.com/inseefrlab/opencode-onyxia>
- **Last synced commit**: `6eff4c7e6ed00ad3af9f4e0ab48a59556ef9ae5b`
- **Last synced**: 2026-09-01
- **Synced verbatim, byte-for-byte at that commit**: `AGENTS.md`, all files under
  `prompts/`, all files under `.opencode/command/`, all files under `.opencode/skills/`
  (every `SKILL.md` plus bundled `assets/`/`references/`/`scripts/`).
- **Re-sync**: `python scripts/sync_upstream_config.py [--ref <branch-or-sha>]` — see that
  script's docstring. It updates this file's "last synced commit" line automatically.

## Directory-naming fix applied during the first sync (2026-09-01)

Before this sync, this repo used `.opencode/skill/` (singular) and nested
`.opencode/prompts/` — divergent from upstream's `.opencode/skills/` (**plural**) and
top-level `prompts/`. Confirmed against upstream's own repo tree, its README.md/INSTALL.md,
and this machine's actual working global install (`~/.config/opencode/`), all three of
which agree on the plural/top-level convention. Because skill auto-discovery is driven by
OpenCode scanning that exact directory name (not an explicit reference we control), the
singular naming plausibly meant our skills were **never actually auto-discovered** by
OpenCode in any benchmark run before this fix — only ever stumbled upon via generic file
exploration. Fixed by renaming the directories and updating the `{file:...}` prompt
references in `configs/layers/guardrails/opencode.patch.json` accordingly.

## Deliberate, tracked deviations from upstream (NOT bugs — do not "fix" on next sync)

**1. Extra `bash` permission allow-rules in `configs/layers/guardrails/opencode.patch.json`.**
Upstream's own top-level (`build`-agent) `permission.bash` block only allow-lists specific
tool invocations (`git *`, `uv *`, `python *`, `ruff *`, etc.) and has **no rule for basic
coreutils** (`ls`, `cat`, `head`, `tail`, `wc`, `grep`, `rg`, `find`, `mkdir`, `echo`, `cd`,
`which`, `pwd`, `sort`, `test`, `true`, `false`, `mv`, bare `R`) — everything else falls to
the `"*": "ask"` catch-all, which in non-interactive `opencode run` (no human to answer
"ask") silently rejects the tool call. Only upstream's read-only `reviewer` subagent has
this coreutils list. We found this empirically from real benchmark runs: it caused ~96% of
one run's rejected tool calls to concentrate in the C4 (guardrails) config, driving
disproportionate timeouts and false "the agent produced nothing" cells that had nothing to
do with the agent's actual capability. We verified the exact same gap exists in upstream's
own config (not a fork-specific bug), so this is a **known upstream limitation**, not
something a fresh sync should silently strip back out. Consider filing this upstream.

The extra rules live in `configs/layers/guardrails/opencode.patch.json`'s `permission.bash`
block, positioned after upstream's broad-allow section and before its narrow
re-restrictions (`git push*`, `rm *`, etc. stay `"ask"` — unchanged from upstream).

**2. Model names left at `qwen3-6-35b-moe` (not upstream's new `qwen3-8-27b`).**
Upstream renamed its default model in the commit synced above. This benchmark's provider
block (`configs/c0_bare/opencode.json`) still declares `qwen3-6-35b-moe` as the available
endpoint, and every real run so far in this project was validated against that model — the
benchmark's model choice is parameterized via `bench run --model`/`-m` independently of
what's declared per-agent in the synced config anyway. Adopting `qwen3-8-27b` is an
operational decision (is it actually deployed on this gateway?), not a config-fidelity one
— left for a human to decide and apply deliberately, not something the sync script should
do automatically.
