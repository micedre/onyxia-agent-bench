#!/usr/bin/env python3
"""Sync configs/layers/{agents_md,skills,commands,guardrails}/root/ with the upstream
inseefrlab/opencode-onyxia config, and flag what changed in the permission/agent blocks
for manual review.

Usage:
    python scripts/sync_upstream_config.py [--ref main]

What it does:
  - Clones upstream at --ref (default: main) to a temp dir.
  - Mirrors (byte-for-byte, deleting anything no longer upstream) AGENTS.md, prompts/,
    .opencode/command/, .opencode/skills/ into the corresponding configs/layers/*/root/
    trees. These have zero benchmark-specific customization, so a straight mirror is safe.
  - Diffs upstream opencode.jsonc's `permission`/`agent` blocks against
    configs/layers/guardrails/opencode.patch.json's, filtering out the deviations already
    tracked in configs/UPSTREAM_SYNC.md (extra coreutils bash allow-rules, model names) so
    genuinely new upstream changes stand out. Prints the diff; does NOT auto-apply it -
    the permission/agent blocks carry benchmark-specific structure (our C1-C4 split,
    OPENCODE_ONYXIA_* env vars) that a mechanical merge could silently corrupt.
  - Updates the "last synced commit"/"last synced" lines in configs/UPSTREAM_SYNC.md.

What it never touches: configs/c0_bare/opencode.json (our provider/env-var block), and the
"deliberate deviations" section of configs/UPSTREAM_SYNC.md itself.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONFIGS_DIR = REPO / "configs"
UPSTREAM_URL = "https://github.com/inseefrlab/opencode-onyxia.git"
SYNC_DOC = CONFIGS_DIR / "UPSTREAM_SYNC.md"

# Paths mirrored verbatim: (upstream-relative, ours-relative-to-configs/layers).
MIRRORS = [
    ("AGENTS.md", "agents_md/root/AGENTS.md"),                 # single file
    ("prompts", "guardrails/root/prompts"),                    # directory
    (".opencode/command", "commands/root/.opencode/command"),  # directory
    (".opencode/skills", "skills/root/.opencode/skills"),      # directory
]

# Keys in the top-level bash permission block that are our tracked deviation (see
# UPSTREAM_SYNC.md) - filtered out of the "needs review" diff since they're expected.
KNOWN_EXTRA_BASH_KEYS = {
    "ls*", "cat*", "head*", "tail*", "wc*", "grep*", "rg*", "find*", "mkdir*", "echo*",
    "cd*", "which*", "pwd*", "sort*", "test*", "true*", "false*", "mv *", "R *",
}


def strip_jsonc_comments(text: str) -> str:
    """Remove // line comments from JSONC, respecting string literals (so URLs like
    https://... inside strings are left alone)."""
    out = []
    in_string = False
    escape = False
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if in_string:
            out.append(c)
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def clone_upstream(ref: str) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="opencode-onyxia-upstream-"))
    subprocess.run(["git", "clone", "--depth", "1", "--branch", ref, UPSTREAM_URL, str(tmp)],
                   check=True, capture_output=True, text=True)
    return tmp


def commit_info(clone_dir: Path) -> tuple[str, str]:
    r = subprocess.run(["git", "-C", str(clone_dir), "log", "-1", "--format=%H"],
                       check=True, capture_output=True, text=True)
    return r.stdout.strip(), dt.date.today().isoformat()


def mirror(src: Path, dst: Path) -> int:
    """Mirror src onto dst (file or dir), deleting anything under dst not in src.
    Returns the number of files that changed (created/updated/removed)."""
    changed = 0
    if src.is_file():
        before = dst.read_bytes() if dst.is_file() else None
        after = src.read_bytes()
        if before != after:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(after)
            changed += 1
        return changed

    src_files = {p.relative_to(src) for p in src.rglob("*") if p.is_file()}
    dst_files = {p.relative_to(dst) for p in dst.rglob("*") if p.is_file()} if dst.is_dir() else set()

    for rel in dst_files - src_files:
        (dst / rel).unlink()
        changed += 1
    for rel in src_files:
        s, d = src / rel, dst / rel
        if not d.is_file() or d.read_bytes() != s.read_bytes():
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
            changed += 1
    # prune now-empty directories left behind by removed files
    if dst.is_dir():
        for d in sorted(dst.rglob("*"), reverse=True):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
    return changed


def diff_bash_permissions(upstream_cfg: dict, ours_cfg: dict) -> None:
    up_bash = upstream_cfg.get("permission", {}).get("bash", {})
    our_bash = ours_cfg.get("permission", {}).get("bash", {})

    up_keys = set(up_bash) - KNOWN_EXTRA_BASH_KEYS
    our_keys = set(our_bash) - KNOWN_EXTRA_BASH_KEYS

    added = up_keys - our_keys
    removed = our_keys - up_keys
    changed_value = {k for k in up_keys & our_keys if up_bash[k] != our_bash[k]}

    if not (added or removed or changed_value):
        print("  bash permission block: no changes beyond our tracked deviations.")
        return
    if added:
        print(f"  NEW upstream rules to consider adding: {sorted(added)}")
    if removed:
        print(f"  rules upstream no longer has (we still do, review if intentional): {sorted(removed)}")
    if changed_value:
        print(f"  rules whose upstream value changed: "
             f"{[(k, our_bash[k], '->', up_bash[k]) for k in sorted(changed_value)]}")


def diff_agent_models(upstream_cfg: dict, ours_cfg: dict) -> None:
    up_agents = upstream_cfg.get("agent", {})
    our_agents = ours_cfg.get("agent", {})
    for name, up_agent in up_agents.items():
        our_agent = our_agents.get(name)
        if our_agent is None:
            print(f"  NEW upstream agent not in our config: {name!r}")
            continue
        up_model, our_model = up_agent.get("model"), our_agent.get("model")
        if up_model != our_model:
            print(f"  agent {name!r} model: ours={our_model!r} upstream={up_model!r} "
                 f"(tracked deviation - see UPSTREAM_SYNC.md; not auto-applied)")
    for name in set(our_agents) - set(up_agents):
        print(f"  agent in our config but not upstream: {name!r} (review)")


def update_sync_doc(sha: str, date: str) -> None:
    text = SYNC_DOC.read_text(encoding="utf-8")
    text = re.sub(r"\*\*Last synced commit\*\*: `[0-9a-f]+`.*",
                 f"**Last synced commit**: `{sha}`", text, count=1)
    text = re.sub(r"\*\*Last synced\*\*: \d{4}-\d{2}-\d{2}",
                 f"**Last synced**: {date}", text, count=1)
    SYNC_DOC.write_text(text, encoding="utf-8")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ref", default="main", help="upstream branch/tag to sync from")
    args = p.parse_args(argv)

    print(f"cloning {UPSTREAM_URL} @ {args.ref} ...")
    clone_dir = clone_upstream(args.ref)
    try:
        sha, date = commit_info(clone_dir)
        print(f"upstream commit: {sha}")

        total_changed = 0
        for src_rel, dst_rel in MIRRORS:
            n = mirror(clone_dir / src_rel, CONFIGS_DIR / "layers" / dst_rel)
            print(f"  mirrored {src_rel:20s} -> {dst_rel}  ({n} file(s) changed)")
            total_changed += n

        print(f"\n{total_changed} file(s) changed by the mirror step.")

        upstream_cfg = json.loads(strip_jsonc_comments((clone_dir / "opencode.jsonc").read_text()))
        ours_cfg = json.loads((CONFIGS_DIR / "layers" / "guardrails" / "opencode.patch.json").read_text())

        print("\npermission/agent review (not auto-applied - see configs/UPSTREAM_SYNC.md):")
        diff_bash_permissions(upstream_cfg, ours_cfg)
        diff_agent_models(upstream_cfg, ours_cfg)

        update_sync_doc(sha, date)
        print(f"\nupdated {SYNC_DOC.relative_to(REPO)} with commit {sha[:12]} / {date}")
        print("\nDone. Review `git diff`, then run the test suite before committing.")
    finally:
        shutil.rmtree(clone_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
