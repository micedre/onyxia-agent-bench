"""Agents pilotables par le harnais : opencode (reference) et Claude Code (`claude -p`).

Un agent = (driver, parseur de sortie, materialisation d'une config dans le workspace).
Les couches de `configs/layers/` restent la source unique : pour Claude Code elles sont
TRADUITES a la volee (pas de copie a maintenir) :

  AGENTS.md                      -> CLAUDE.md
  .opencode/skills/*             -> .claude/skills/*              (meme format SKILL.md)
  .opencode/command/*            -> abandonne (inerte en non interactif, cf. README)
  opencode.patch.json `agent.*`  -> .claude/agents/<nom>.md       (prompts `{file:...}` inlines)
  agent `build` (prompt primaire)-> ajoute a CLAUDE.md            (pas d'equivalent d'agent primaire)
  permission.bash / webfetch     -> .claude/settings.json permissions

Regles d'equite (a garder alignees avec le README) :
  * en headless personne ne repond a un `ask` : tout ce qui n'est pas explicitement autorise est
    refuse, comme opencode le fait implicitement (cf. docs/UPSTREAM_FINDINGS.md, rejets de C4).
    `*: ask` disparait donc (plus de `Bash` nu) et les `ask`/`deny` nommes vont en `deny`.
  * C0 recoit la meme permission large que la config opencode nue : edition et shell autorises,
    web refuse.
  * ne se traduisent pas : modele/temperature/top_p/steps par agent (`model: inherit`), allowlist
    bash propre au sous-agent `reviewer` (reste en lecture seule par son prompt et sa liste d'outils).
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from bench.configs import (
    LAYER_FILES_MANIFEST,
    _exclude_from_git,
    _sha256,
)
from bench.configs import (
    materialize as materialize_opencode,
)
from bench.opencode_driver import parse_output as parse_opencode_output
from bench.schema import ConfigSpec, Transcript

AGENTS = ("opencode", "claude")

_FILE_REF = re.compile(r"\{file:([^}]+)\}")
_CLAUDE_BASE_ALLOW = ["Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob", "Grep",
                      "TodoWrite"]
# Sous-agents en lecture seule (outils restreints cote Claude).
_READONLY_TOOLS = {"reviewer": "Read, Grep, Glob, Bash", "dataviz-vision": "Read"}


def parse_transcript(agent: str, raw: str) -> Transcript:
    if agent == "claude":
        from bench.claude_driver import parse_claude_stream
        return parse_claude_stream(raw)
    return parse_opencode_output(raw)


def make_driver(agent: str):
    if agent == "claude":
        from bench.claude_driver import ClaudeCodeDriver
        return ClaudeCodeDriver()
    from bench.opencode_driver import RealOpenCodeDriver
    return RealOpenCodeDriver()


def materialize(agent: str, config: ConfigSpec, configs_dir: Path, base: str, workspace: Path,
                model: str | None = None) -> Path:
    if agent == "claude":
        return materialize_claude(config, configs_dir, workspace)
    return materialize_opencode(config, configs_dir, base, workspace, model=model)


# --------------------------------------------------------------------------------------
# Claude Code
# --------------------------------------------------------------------------------------
def _resolve_prompt(template: str, layer_roots: list[Path]) -> str:
    """Remplace chaque `{file:./chemin}` par le contenu du fichier trouve dans une couche."""
    def sub(m: re.Match) -> str:
        rel = m.group(1).strip().removeprefix("./")
        for root in layer_roots:
            f = root / rel
            if f.is_file():
                return f.read_text(encoding="utf-8").strip()
        raise FileNotFoundError(f"prompt {rel!r} introuvable dans les couches {layer_roots}")
    return _FILE_REF.sub(sub, template)


def _bash_rules(bash: dict) -> tuple[list[str], list[str], bool]:
    """(allow, deny, wildcard_allow) depuis la table `permission.bash` d'opencode."""
    allow: list[str] = []
    deny: list[str] = []
    wildcard_allow = False
    for pattern, action in bash.items():
        if pattern == "*":
            wildcard_allow = action == "allow"
        elif action == "allow":
            allow.append(f"Bash({pattern})")
        else:  # ask | deny : refuse en headless
            deny.append(f"Bash({pattern})")
    return allow, deny, wildcard_allow


def _frontmatter_value(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # une chaine JSON est du YAML valide


def materialize_claude(config: ConfigSpec, configs_dir: Path, workspace: Path) -> Path:
    """Traduit les couches de `config` en CLAUDE.md, .claude/skills, .claude/agents et
    .claude/settings.json. Renvoie le chemin de settings.json. Ecrit le meme manifeste de hash
    qu'opencode pour que la notation exclue ces fichiers tant qu'ils sont intacts."""
    layer_roots = [configs_dir / "layers" / l / "root" for l in config.layers]
    patch: dict = {}
    from bench.configs import _deep_merge
    for layer in config.layers:
        p = configs_dir / "layers" / layer / "opencode.patch.json"
        if p.exists():
            _deep_merge(patch, json.loads(p.read_text(encoding="utf-8")))

    written: list[Path] = []

    def put(rel: str, text: str):
        f = workspace / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
        written.append(f)

    claude_md: list[str] = []
    has_skills = False
    for root in layer_roots:
        if not root.is_dir():
            continue
        agents_md = root / "AGENTS.md"
        if agents_md.is_file():
            claude_md.append(agents_md.read_text(encoding="utf-8").strip())
        skills = root / ".opencode" / "skills"
        if skills.is_dir():
            has_skills = True
            for src in skills.rglob("*"):
                if src.is_file():
                    dst = workspace / ".claude" / "skills" / src.relative_to(skills)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    written.append(dst)

    agents = patch.get("agent", {})
    # Prompt de l'agent primaire `build` (base + build + contrat de fin) : opencode le charge
    # comme prompt systeme ; Claude Code n'a pas d'agent primaire configurable -> CLAUDE.md.
    build = agents.get("build")
    if build and build.get("prompt"):
        claude_md.append(_resolve_prompt(build["prompt"], layer_roots))
    if claude_md:
        put("CLAUDE.md", "\n\n".join(claude_md) + "\n")

    has_subagents = False
    for name, spec in agents.items():
        if spec.get("mode") != "subagent":
            continue
        has_subagents = True
        fm = ["---", f"name: {name}", f"description: {_frontmatter_value(spec.get('description', name))}",
              "model: inherit"]
        if name in _READONLY_TOOLS:
            fm.append(f"tools: {_READONLY_TOOLS[name]}")
        fm.append("---")
        put(f".claude/agents/{name}.md",
            "\n".join(fm) + "\n\n" + _resolve_prompt(spec["prompt"], layer_roots) + "\n")

    perm = patch.get("permission", {})
    allow = list(_CLAUDE_BASE_ALLOW)
    deny: list[str] = []
    bash = perm.get("bash")
    if isinstance(bash, dict):
        b_allow, b_deny, wildcard = _bash_rules(bash)
        allow += (["Bash"] if wildcard else []) + b_allow
        deny += b_deny
    else:
        # config nue : meme permission que l'opencode.json de base (`bash: allow`)
        allow.append("Bash")
    # web : refuse dans la config nue ; `ask` (guardrails) vaut refus en headless
    deny += ["WebFetch", "WebSearch"]
    if has_skills:
        allow.append("Skill")
    if has_subagents:
        allow.append("Task")
    settings = {"permissions": {"allow": allow, "deny": deny}}
    out = workspace / ".claude" / "settings.json"
    put(".claude/settings.json", json.dumps(settings, indent=2, ensure_ascii=False))

    hashes = {f.relative_to(workspace).as_posix(): _sha256(f) for f in written}
    (workspace / LAYER_FILES_MANIFEST).write_text(
        json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")
    _exclude_from_git(workspace, list(hashes) + [LAYER_FILES_MANIFEST])
    return out
