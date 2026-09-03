# Sync status vs. `inseefrlab/opencode-onyxia`

This benchmark's `configs/layers/{agents_md,skills,commands,guardrails}/root/` content is
sourced from the upstream `opencode-onyxia` repository, so that the ablation ladder tests
the *real* config data-scientists actually run, not a hand-maintained approximation of it.

- **Upstream repo**: <https://github.com/inseefrlab/opencode-onyxia>
- **Last synced commit**: `39d7071850a6269b7c88a3f9e4ad5dc57333dcd4`
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

**2. Default model and per-agent model fields left at `qwen3-6-35b-moe` (not upstream's
new `qwen3-8-27b`).** Upstream renamed its default model in the commit synced above. This
benchmark's provider block (`configs/c0_bare/opencode.json`) now also declares
`qwen3-8-27b` as an available model (confirmed present on the gateway's `/models` endpoint
and working via a direct `opencode run -m onyxia/qwen3-8-27b` test) - `bench run --model
onyxia/qwen3-8-27b` works. The top-level default model and every per-agent `"model"` field
in `configs/layers/guardrails/opencode.patch.json` (build, plan, python-ds, r-ds, mlops,
reviewer) still say `qwen3-6-35b-moe`/`gemma4-26b-moe`, matching what every real run so far
in this project was validated against - switching the *default* is still an operational
decision left for a human. **Open caveat, not yet verified**: whether `--model`/`-m`
actually overrides those per-agent fields for subagent delegation (see the README's own
note on this) - if it doesn't, running `bench run --model onyxia/qwen3-8-27b` exercises the
new model for the primary `build` agent but subagents (python-ds, r-ds, mlops, reviewer)
could silently keep running on the old model. Worth confirming with a real run before
trusting a qwen3-8-27b comparison that involves subagent delegation.

**3. (Known gap, not yet handled) top-level `"edit": "allow"` survives into C4.**
`configs/c0_bare/opencode.json` grants `edit`/`bash` at top level so that the bare C0 agent can
work at all. `bench/configs.py::_deep_merge` only overwrites keys the patch mentions, and
upstream's patch deliberately has *no* top-level `edit` key (its own comment explains that a
global `edit: allow` overrides a subagent's `edit: deny`, so `reviewer`/`dataviz-vision` stop
being read-only). The materialised C4 config therefore carries `"edit": "allow"` that upstream
does not have. Planned fix (not done yet): let a patch delete a key (e.g. JSON `null` value in
`opencode.patch.json`) and use it for `edit` in the guardrails layer. See
`docs/UPSTREAM_FINDINGS.md` §7 for the wider context and the measurements behind it.
