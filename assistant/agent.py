"""Claude tool-use loop for the fantasy GM assistant.

The model never computes numbers itself: it decides which tools to call (what-if tools on the auction
board, news tools) and explains the results. Every turn is logged to logs/assistant_*.jsonl with the
tool calls, results and token usage, for evaluation and cost tracking.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

from .engine import ROOT, Board
from .rules import rules_text
from .tools import news
from .tools.whatif import TOOL_SPECS as WHATIF_SPECS
from .tools.whatif import WhatIf

DEFAULT_MODEL = "claude-sonnet-5-5"
LOG_DIR = ROOT / "logs"



def load_env(path: Path = ROOT / ".env"):
    """Minimal .env reader (KEY=value lines), so no extra dependency is needed."""
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class Toolbox:
    """Maps tool names to Python functions. Usable with or without the LLM (see cli.py --tool)."""

    def __init__(self, board: Board):
        self.board = board
        self.whatif = WhatIf(board)
        self.specs = WHATIF_SPECS + news.TOOL_SPECS

    def call(self, name: str, args: dict):
        if hasattr(self.whatif, name) and any(s["name"] == name for s in WHATIF_SPECS):
            return getattr(self.whatif, name)(**args)
        if name == "news_impact":
            return news.news_impact(self.board, **args)
        if name in ("injury_history", "latest_injury_report", "rss_news", "search_news"):
            return getattr(news, name)(**args)
        raise ValueError(f"Unknown tool {name}")


class Agent:
    def __init__(self, board: Board, model: str | None = None, max_tool_rounds: int = 8, log: bool = True):
        load_env()
        import anthropic  # imported here so the tools work without the SDK installed
        self.client = anthropic.Anthropic()
        self.model = model or os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)
        self.tools = Toolbox(board)
        self.max_rounds = max_tool_rounds
        self.system = rules_text(board)
        self.messages: list = []
        self.log_path = None
        if log:
            LOG_DIR.mkdir(exist_ok=True)
            self.log_path = LOG_DIR / f"assistant_{datetime.now():%Y%m%d_%H%M%S}.jsonl"

    def _log(self, rec: dict):
        if self.log_path:
            with self.log_path.open("a") as f:
                f.write(json.dumps(rec, default=str) + "\n")

    def ask(self, question: str) -> dict:
        """One manager question -> answer text, plus the tool calls and token usage behind it."""
        self.messages.append({"role": "user", "content": question})
        calls, usage, t0 = [], {"input_tokens": 0, "output_tokens": 0}, time.time()
        for _ in range(self.max_rounds):
            resp = self.client.messages.create(
                model=self.model, max_tokens=1500,
                system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
                tools=self.tools.specs, messages=self.messages)
            usage["input_tokens"] += resp.usage.input_tokens
            usage["output_tokens"] += resp.usage.output_tokens
            self.messages.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})
            if resp.stop_reason != "tool_use":
                break
            results = []
            for b in resp.content:
                if b.type != "tool_use":
                    continue
                try:
                    out, err = self.tools.call(b.name, b.input), False
                except Exception as e:  # tool errors go back to the model so it can correct itself
                    out, err = {"error": f"{type(e).__name__}: {e}"}, True
                payload = json.dumps(out, default=str, ensure_ascii=False)
                calls.append({"tool": b.name, "input": b.input, "error": err, "output": payload})
                results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": err,
                                "content": payload})
            self.messages.append({"role": "user", "content": results})
        else:
            calls.append({"tool": None, "error": "max tool rounds reached"})
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        rec = {"time": datetime.now().isoformat(), "model": self.model, "question": question, "answer": text,
               "tool_calls": calls, "usage": usage, "seconds": round(time.time() - t0, 1)}
        self._log(rec)
        return rec
