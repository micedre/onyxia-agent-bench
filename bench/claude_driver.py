"""Pilotage de Claude Code en mode headless (`claude -p`).

ClaudeCodeDriver : meme contrat que RealOpenCodeDriver (BaseDriver.run -> RunResult), pour
comparer un modele frontier au modele auto-heberge sur les memes taches, graders et seeds, en
isolation "legere" (sous-repertoire + git sur l'hote).
Isolation "legere" uniquement : `--isolation pod` n'est pas encore supporte pour cet agent (l'image
doit contenir le binaire `claude`).

Format `--output-format stream-json --verbose` : nd-JSON, un objet par ligne.
  {"type":"system","subtype":"init", ...}
  {"type":"assistant","message":{"content":[{"type":"text","text":"..."},
                                            {"type":"tool_use","id":"toolu_..","name":"Bash",
                                             "input":{...}}],
                                 "usage":{"input_tokens":..,"output_tokens":..,
                                          "cache_read_input_tokens":..,
                                          "cache_creation_input_tokens":..}}}
  {"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_..",
                                        "content":"..."|[{"type":"text","text":".."}],
                                        "is_error":bool}]}}
  {"type":"result","subtype":"success|error_*","is_error":bool,"num_turns":N,
   "total_cost_usd":x,"usage":{...},"permission_denials":[...]}
Un message `assistant` = un tour LLM. Comme pour opencode, `tokens_in` somme l'entree de chaque
tour (cache inclus : `input_tokens` + `cache_read` + `cache_creation`), donc `tokens_total` reste
comparable d'un agent a l'autre ; `tokens_cache_read` est aussi expose a part.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from bench.opencode_driver import BaseDriver, _as_int
from bench.schema import Event, RunResult, Transcript

_OUTPUT_KEEP = 4000  # caracteres de sortie d'outil conserves par evenement

def _tool_result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return ""


def _usage_in(usage: dict) -> int:
    return (_as_int(usage.get("input_tokens")) + _as_int(usage.get("cache_read_input_tokens"))
            + _as_int(usage.get("cache_creation_input_tokens")))


def parse_claude_stream(stdout: str) -> Transcript:
    """Transforme la sortie stream-json de `claude -p` en Transcript (meme contrat que
    `opencode_driver.parse_output`). Les lignes non JSON (warnings...) sont ignorees."""
    t = Transcript(raw_stdout=stdout)
    tool_events: dict[str, Event] = {}
    result: dict | None = None
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        typ = obj.get("type")
        if typ == "assistant":
            msg = obj.get("message") or {}
            # les sous-agents (outil Task) emettent aussi des messages, marques par
            # `parent_tool_use_id` : ils comptent comme tours/tokens, pas comme texte final.
            sub = bool(obj.get("parent_tool_use_id"))
            t.assistant_turns += 1
            turn = t.assistant_turns
            usage = msg.get("usage") or {}
            inp = _usage_in(usage)
            t.tokens_in += inp
            t.tokens_out += _as_int(usage.get("output_tokens"))
            t.tokens_cache_read += _as_int(usage.get("cache_read_input_tokens"))
            t.tokens_cache_write += _as_int(usage.get("cache_creation_input_tokens"))
            if inp and not sub:
                if not t.context_tokens_first:
                    t.context_tokens_first = inp
                t.context_tokens_last = inp
            for block in msg.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text") and not sub:
                    t.events.append(Event("message", text=str(block["text"]), raw=obj, turn=turn))
                elif block.get("type") == "tool_use":
                    name = str(block.get("name") or "tool")
                    ev = Event("tool", name=name,
                               text=json.dumps(block.get("input", {}), ensure_ascii=False)[:300],
                               raw=obj, turn=turn)
                    t.events.append(ev)
                    tool_events[str(block.get("id"))] = ev
                    if name in ("Task", "Agent"):
                        t.subagent_calls += 1
        elif typ == "user":
            for block in (obj.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    ev = tool_events.get(str(block.get("tool_use_id")))
                    if ev is None:
                        continue
                    ev.output = _tool_result_text(block.get("content"))[:_OUTPUT_KEEP]
                    if block.get("is_error"):
                        ev.status = "error"
                        t.tool_errors += 1
                    else:
                        ev.status = "completed"
        elif typ == "result":
            result = obj

    if result is not None:
        try:
            t.cost = float(result.get("total_cost_usd") or 0)
        except (TypeError, ValueError):
            pass
        # refus de permission : en headless une action non autorisee est refusee d'office
        # (pas de humain pour repondre a un `ask`) - c'est l'equivalent du "rejected permission"
        # d'opencode, et c'est ce que les garde-fous de C4 provoquent.
        denials = result.get("permission_denials")
        if isinstance(denials, list):
            t.permission_rejections = len(denials)
        if not t.assistant_turns:
            t.assistant_turns = _as_int(result.get("num_turns"))
        # filet : si aucun message assistant n'a ete vu mais `result` porte le texte final
        if not t.message_events and isinstance(result.get("result"), str) and result["result"]:
            t.events.append(Event("message", text=result["result"], raw=result,
                                  turn=max(1, t.assistant_turns)))
    # Texte assistant uniquement (jamais les sorties d'outils) - cf. parse_output.
    t.text = "\n".join(e.text for e in t.events if e.type == "message")
    return t


def build_command(binary: str, prompt: str, model: str) -> list[str]:
    cmd = [binary, "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--model", model,
           # n'utiliser que le .claude/settings.json du workspace (permissions ecrites par
           # bench/agents.py, jamais ~/.claude/settings.json) et aucun serveur MCP herite :
           # seules nos couches doivent varier entre C0 et C4.
           "--setting-sources", "project", "--strict-mcp-config",
           "--no-session-persistence"]
    return cmd


class ClaudeCodeDriver(BaseDriver):
    name = "claude"

    def __init__(self, binary: str = "claude", extra_env: dict | None = None):
        self.binary = binary
        self.extra_env = extra_env or {}

    def _isolated_config_dir(self) -> str:
        """CLAUDE_CONFIG_DIR temporaire : sans ca, les skills/CLAUDE.md/agents utilisateur de
        ~/.claude s'appliquent a TOUTES les cellules, C0 compris (meme piege que XDG_* pour
        opencode). Sans ANTHROPIC_API_KEY, on recopie les identifiants OAuth pour rester
        authentifie."""
        d = tempfile.mkdtemp(prefix="bench-claude-")
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")):
            cred = Path.home() / ".claude" / ".credentials.json"
            if cred.is_file():
                shutil.copy2(cred, Path(d) / ".credentials.json")
        return d

    def run(self, task, workspace, model, seed, config_id) -> RunResult:
        env = os.environ.copy()
        env.update(self.extra_env)
        # Ne pas heriter du contexte d'une session Claude Code parente.
        # (mais pas le jeton OAuth : c'est l'authentification de la cellule)
        for k in [k for k in env if (k.startswith("CLAUDE_CODE_") and k != "CLAUDE_CODE_OAUTH_TOKEN")
                  or k in ("CLAUDECODE", "CLAUDE_PID")]:
            env.pop(k)
        env["PWD"] = str(workspace)
        cfg_dir = self._isolated_config_dir()
        env["CLAUDE_CONFIG_DIR"] = cfg_dir
        cmd = build_command(self.binary, task.prompt, model)
        t0 = time.time()
        timed_out = False
        rc = 0
        out, err = "", ""

        def _decode(x: bytes | str | None) -> str:
            return x.decode("utf-8", errors="replace") if isinstance(x, bytes) else (x or "")

        try:
            try:
                r = subprocess.run(cmd, cwd=workspace, env=env, capture_output=True, text=True,
                                   timeout=task.timeout_s, stdin=subprocess.DEVNULL)
                rc, out, err = r.returncode, r.stdout, r.stderr
            except subprocess.TimeoutExpired as e:
                timed_out = True
                rc = 124
                out = _decode(e.stdout)
                err = _decode(e.stderr) + "\n[TIMEOUT]"
            except FileNotFoundError:
                return RunResult(task.id, config_id, model, seed, workspace, Transcript(text=""),
                                 exit_code=127, wall_clock_s=0.0,
                                 error=f"binaire '{self.binary}' introuvable (Claude Code installe ?)")
        finally:
            shutil.rmtree(cfg_dir, ignore_errors=True)
        dt = time.time() - t0
        transcript = parse_claude_stream(out)
        if err:
            transcript.raw_stdout += "\n[STDERR]\n" + err
        res = RunResult(task.id, config_id, model, seed, workspace, transcript,
                        exit_code=rc, timed_out=timed_out, wall_clock_s=dt,
                        error=None if rc == 0 else f"exit={rc}")
        res.agent_s = dt
        return res

