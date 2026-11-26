"""Tests for the MCP server, run as a real subprocess speaking JSON-RPC over stdio (no Claude needed).

Run: python3 -m assistant.tests.test_mcp_server
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "assistant" / "eval" / "mid_auction_state.json"
RESULTS = []


def check(cond, msg):
    RESULTS.append(bool(cond))
    print(("ok   " if cond else "FAIL ") + msg)


class Client:
    def __init__(self, *args):
        self.p = subprocess.Popen([sys.executable, "-m", "assistant.mcp_server", *args], cwd=ROOT,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.n = 0

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            self.n += 1
            msg["id"] = self.n
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()
        return None if notify else json.loads(self.p.stdout.readline())

    def call(self, name, args):
        r = self.send("tools/call", {"name": name, "arguments": args})["result"]
        return json.loads(r["content"][0]["text"]), r["isError"]

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=10)
        return self.p.stderr.read()


def main():
    c = Client("--state", str(STATE))
    init = c.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "test", "version": "0"}})["result"]
    check(init["serverInfo"]["name"] == "fantasy-gm" and init["protocolVersion"] == "2025-06-18", "initialize")
    check("my max is $0" in init["instructions"] and "14-team" in init["instructions"], "rules sent as server instructions")
    check(c.send("notifications/initialized", notify=True) is None, "notification gets no reply")
    tools = c.send("tools/list")["result"]["tools"]
    check(len(tools) == 15 and all("inputSchema" in t for t in tools), "15 tools listed with input schemas")
    s, err = c.call("team_summary", {})
    check(not err and s["budget_left"] == 90 and s["open_spots"] == 8, "team_summary from the --state file")
    t, err = c.call("find_targets", {"position": "PF", "need_categories": ["3PM"], "budget_left": 40, "open_spots": 1, "limit": 3})
    check([x["name"] for x in t["targets"]] == ["Jayson Tatum", "Kevin Durant", "Brandon Miller"], "Elliot's question: same top 3 as the engine")
    e, err = c.call("player_card", {"name": "LeBron Jamesson"})
    check(err and "LeBron James" in e["error"], "unknown player -> isError with close names")
    check(c.send("tools/call", {"name": "drop_table", "arguments": {}})["result"]["isError"], "unknown tool -> isError")
    check(c.send("bogus/method")["error"]["code"] == -32601, "unknown method -> JSON-RPC error")
    check(c.send("ping")["result"] == {}, "ping")
    stderr = c.close()
    check("team_summary" in stderr, "tool calls logged to stderr, not stdout")

    # state file persists sales across server restarts (each `claude -p` turn starts a new server)
    with tempfile.TemporaryDirectory() as d:
        sf = Path(d) / "live.json"
        sf.write_text(STATE.read_text())
        c = Client("--state-file", str(sf))
        c.send("initialize", {"protocolVersion": "2024-11-05"})
        _, err = c.call("record_sale", {"player": "Trey Murphy III", "team": "me", "price": 46})
        check(not err and any(s["pid"] for s in json.loads(sf.read_text())["sales"]), "record_sale saved to state file")
        c.close()
        c = Client("--state-file", str(sf))
        c.send("initialize", {"protocolVersion": "2024-11-05"})
        s, _ = c.call("team_summary", {})
        check(s["budget_left"] == 44 and s["open_spots"] == 7, "restarted server sees the sale ($44, 7 spots)")
        c.call("punt_scenario", {"categories": ["FT"]})
        check(json.loads(sf.read_text())["weights"]["FT"] == 1, "hypothetical punt not saved")
        c.call("undo_sale", {"player": "Trey Murphy III"})
        c.close()
        c = Client("--state-file", str(sf))
        c.send("initialize", {})
        s, _ = c.call("team_summary", {})
        check(s["budget_left"] == 90, "undo persisted too")
        c.close()

    out = subprocess.run([sys.executable, "-m", "assistant.mcp_server", "--print-rules"], cwd=ROOT,
                         capture_output=True, text=True).stdout
    check("Rules:" in out, "--print-rules")
    print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
