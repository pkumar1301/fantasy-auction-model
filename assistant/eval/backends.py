"""Two ways to run the assistant for evaluation, with the same interface:

  api          Claude API through assistant/agent.py (needs ANTHROPIC_API_KEY)
  claude-code  the Claude Code CLI in print mode (`claude -p`) with the MCP server, so it runs on a Claude
               subscription with no API key. Built-in tools are switched off and the system prompt is
               replaced with the assistant's rules, so Claude can only answer through the fantasy-gm tools.

A session = one conversation from a given auction state:
    s = make_backend("claude-code").session(state_dict)
    r = s.ask("question")   # {answer, tool_calls[{tool, input, output, error}], usage, seconds, cost_usd}
    s.board()               # the auction state after the turn (for checks like "budget is now $44")
    s.close()
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from assistant.engine import ROOT, Board, load_board_data
from assistant.rules import rules_text

SERVER = "fantasy-gm"


class APISession:
    def __init__(self, state, model=None):
        from assistant.agent import Agent
        self.agent = Agent(Board(load_board_data(), state), model=model)

    def ask(self, q):
        r = self.agent.ask(q)
        r.setdefault("cost_usd", None)
        return r

    def board(self):
        return self.agent.tools.board

    def close(self):
        pass


class ClaudeCodeSession:
    def __init__(self, state, model=None, max_turns=12, timeout=300):
        if not shutil.which("claude"):
            raise RuntimeError("The `claude` CLI isn't installed. Install Claude Code and sign in with your subscription.")
        self.dir = Path(tempfile.mkdtemp(prefix="gm_eval_"))
        self.state_file = self.dir / "state.json"
        self.state_file.write_text(json.dumps(state or {}))
        cfg = {"mcpServers": {SERVER: {"command": sys.executable,
                                       "args": ["-m", "assistant.mcp_server", "--state-file", str(self.state_file)],
                                       "env": {"PYTHONPATH": str(ROOT)}}}}
        self.cfg = self.dir / "mcp.json"
        self.cfg.write_text(json.dumps(cfg))
        self.rules = self.dir / "rules.txt"
        self.rules.write_text(rules_text(Board(load_board_data(), state)))
        self.model, self.max_turns, self.timeout = model, max_turns, timeout
        self.session_id = None

    def ask(self, q):
        cmd = ["claude", "-p", q, "--output-format", "stream-json", "--verbose",
               "--mcp-config", str(self.cfg), "--strict-mcp-config",
               "--tools", "", "--allowedTools", f"mcp__{SERVER}",
               "--system-prompt-file", str(self.rules), "--max-turns", str(self.max_turns)]
        if self.model:
            cmd += ["--model", self.model]
        if self.session_id:
            cmd += ["--resume", self.session_id]
        t0 = time.time()
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout, cwd=self.dir)
        r = parse_stream(p.stdout)
        r["seconds"] = round(time.time() - t0, 1)
        if r.get("session_id"):
            self.session_id = r["session_id"]
        if not r["answer"] and p.returncode != 0:
            r["answer"] = f"[claude exited {p.returncode}: {(p.stderr or p.stdout)[-400:]}]"
        return r

    def board(self):
        return Board(load_board_data(), json.loads(self.state_file.read_text()))

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def parse_stream(text: str) -> dict:
    """Turn `claude -p --output-format stream-json` output into the same record the API agent returns."""
    calls, by_id, answer, usage, cost, sid = [], {}, "", {"input_tokens": 0, "output_tokens": 0}, None, None
    for line in text.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        sid = ev.get("session_id") or sid
        msg = ev.get("message") or {}
        if ev.get("type") == "assistant":
            for b in msg.get("content", []):
                if b.get("type") == "tool_use":
                    name = b.get("name", "")
                    c = {"tool": name.split("__")[-1] if name.startswith("mcp__") else name,
                         "input": b.get("input"), "error": False, "output": ""}
                    calls.append(c)
                    by_id[b.get("id")] = c
        elif ev.get("type") == "user":
            for b in msg.get("content", []) if isinstance(msg.get("content"), list) else []:
                if b.get("type") == "tool_result" and b.get("tool_use_id") in by_id:
                    c = by_id[b["tool_use_id"]]
                    content = b.get("content")
                    c["output"] = content if isinstance(content, str) else \
                        "".join(x.get("text", "") for x in content or [] if isinstance(x, dict))
                    c["error"] = bool(b.get("is_error"))
        elif ev.get("type") == "result":
            answer = ev.get("result") or ""
            u = ev.get("usage") or {}
            usage = {"input_tokens": (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                     + (u.get("cache_creation_input_tokens") or 0), "output_tokens": u.get("output_tokens") or 0}
            cost = ev.get("total_cost_usd")
    return {"answer": answer.strip(), "tool_calls": calls, "usage": usage, "cost_usd": cost, "session_id": sid}


class Backend:
    def __init__(self, kind, model=None):
        self.kind, self.model = kind, model

    def session(self, state):
        if self.kind == "api":
            return APISession(state, self.model)
        return ClaudeCodeSession(state, self.model)


def make_backend(kind="api", model=None) -> Backend:
    if kind not in ("api", "claude-code"):
        raise ValueError("backend must be 'api' or 'claude-code'")
    return Backend(kind, model)
