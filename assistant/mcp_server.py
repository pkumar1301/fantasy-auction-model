"""MCP server: use the GM assistant through a Claude subscription (Claude desktop app or Claude Code),
with no API key. Claude does the reasoning; this server supplies every number through the same 15 tools
the API agent uses.

  python3 -m assistant.mcp_server                       stdio server (Claude starts it; see assistant/README.md)
  python3 -m assistant.mcp_server --state my.json       start from a board Export file
  python3 -m assistant.mcp_server --state-file live.json
        keep the auction state in a file: loaded at start, saved after record_sale / undo_sale /
        set_category_weights, so sales survive restarts (each `claude -p` run starts a new server)
  python3 -m assistant.mcp_server --print-rules         the rules text, to paste into a Claude Project's instructions

Standard library only (JSON-RPC 2.0 over stdin/stdout, one message per line), so nothing extra to install.
Logs go to stderr; stdout carries protocol messages only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import Toolbox
from .engine import DEFAULT_DATA, Board, load_board_data
from .rules import rules_text

SERVER_NAME = "fantasy-gm"
VERSION = "0.2.0"
PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]
MUTATING = {"record_sale", "undo_sale", "set_category_weights"}


def log(*a):
    print(f"[{SERVER_NAME}]", *a, file=sys.stderr, flush=True)


class Server:
    def __init__(self, board: Board, state_file: Path | None = None):
        self.board = board
        self.tools = Toolbox(board)
        self.state_file = state_file

    def _save(self):
        if self.state_file:
            self.state_file.write_text(json.dumps(self.board.export_state(), indent=1))

    def handle(self, msg: dict) -> dict | None:
        """One JSON-RPC message in, one response out (None for notifications)."""
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if mid is None:                       # notification (e.g. notifications/initialized): no reply
            return None
        try:
            if method == "initialize":
                asked = params.get("protocolVersion")
                result = {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": SERVER_NAME, "version": VERSION},
                          "instructions": rules_text(self.board)}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [{"name": s["name"], "description": s["description"],
                                     "inputSchema": s["input_schema"]} for s in self.tools.specs]}
            elif method == "tools/call":
                name, args = params.get("name"), params.get("arguments") or {}
                try:
                    out, err = self.tools.call(name, args), False
                    if name in MUTATING:
                        self._save()
                except Exception as e:   # tool errors go back to Claude as content, like the API agent
                    out, err = {"error": f"{type(e).__name__}: {e}"}, True
                log(f"{name} {json.dumps(args)[:120]}{' ERROR' if err else ''}")
                result = {"content": [{"type": "text", "text": json.dumps(out, default=str, ensure_ascii=False)}], "isError": err}
            elif method in ("resources/list", "prompts/list"):
                result = {method.split("/")[0]: []}
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def serve(self, inp=sys.stdin, out=sys.stdout):
        for line in inp:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                out.write(json.dumps({"jsonrpc": "2.0", "id": None,
                                      "error": {"code": -32700, "message": "Parse error"}}) + "\n")
                out.flush()
                continue
            for m in (msg if isinstance(msg, list) else [msg]):
                resp = self.handle(m)
                if resp is not None:
                    out.write(json.dumps(resp, default=str) + "\n")
                    out.flush()


def build_board(board_path=None, state=None, state_file=None) -> Board:
    data = load_board_data(board_path or DEFAULT_DATA)
    st = None
    if state_file and Path(state_file).exists() and Path(state_file).stat().st_size > 0:
        st = json.loads(Path(state_file).read_text())
    elif state:
        st = json.loads(Path(state).read_text())
    return Board(data, st)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", default=str(DEFAULT_DATA))
    ap.add_argument("--state", help="initial auction state (board Export file); read only")
    ap.add_argument("--state-file", help="persistent state: loaded if present, saved after every change")
    ap.add_argument("--print-rules", action="store_true")
    a = ap.parse_args(argv)
    board = build_board(a.board, a.state, a.state_file)
    if a.print_rules:
        print(rules_text(board))
        return 0
    log(f"ready: {len(board.P)} players, {len(board.st['sales'])} sales, state file {a.state_file or 'none'}")
    Server(board, Path(a.state_file) if a.state_file else None).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
