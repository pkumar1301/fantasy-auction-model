"""Parity test: the browser tools (gm_core.js) must answer exactly like the Python tools.

  python3 -m assistant.web.parity_test          (needs node; run build_data first)

Runs every evaluation question's reference call, the conversation scripts' state changes, a set of news
texts and the error cases through both, from the same auction states, and compares every field
(numbers to 1e-6). Error cases must fail in both, with the same message.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from assistant.agent import Toolbox
from assistant.engine import Board, load_board_data
from assistant.tools import news

HERE = Path(__file__).resolve().parent
EVAL = HERE.parent / "eval"
TOL = 1e-6

NEWS_TEXTS = ["Left calf strain, out 2-3 weeks", "Right hamstring strain", "torn ACL", "out for season",
              "done for the season", "will miss the rest of the season", "out indefinitely with a knee injury",
              "out through the All-Star break", "back after the break", "OG Anunoby Set to Miss Third Straight Game",
              "Ruled out for Wednesday", "Knicks Announce Major Injury Update", "suffered a break in his left wrist",
              "lower back spasms", "Rest", "day-to-day with knee soreness", "expected to miss 6-8 weeks",
              "shut down for the year", "will undergo season-ending surgery", "Illness", "sprained ankle, out two weeks",
              "no timetable for his return", ""]


def cases():
    st = json.loads((EVAL / "mid_auction_state.json").read_text())
    out = []
    for f in ["questions.jsonl", "questions_muse.jsonl"]:
        p = EVAL / f
        if p.exists():
            for line in p.read_text().splitlines():
                q = json.loads(line) if line.strip() else None
                if q and q["tool"] not in ("rss_news", "search_news", "latest_injury_report"):
                    out.append({"id": q["id"], "state": st, "steps": [[q["tool"], q["ref"]]]})
    for name in ["Trey Murphy III", "OG Anunoby", "Jayson Tatum", "Ja Morant", "Jalen Duren", "Nikola Jokic"]:
        for t in NEWS_TEXTS:
            out.append({"id": f"news {name} {t[:20]}", "state": st, "steps": [["news_impact", {"player": name, "news_text": t}]]})
    out += [
        {"id": "minutes +15", "state": st, "steps": [["news_impact", {"player": "Ja Morant", "minutes_change_pct": 15}]]},
        {"id": "games given", "state": st, "steps": [["news_impact", {"player": "Jalen Duren", "news_text": "torn ACL", "games_missed": 200}]]},
        {"id": "empty board summary", "state": None, "steps": [["team_summary", {}]]},
        {"id": "empty board targets", "state": None, "steps": [["find_targets", {"position": "C", "limit": 10}]]},
        {"id": "D1 flow", "state": st, "steps": [["team_summary", {}], ["bid_check", {"name": "Trey Murphy III", "price": 40}],
                                                 ["record_sale", {"player": "Trey Murphy III", "team": "me", "price": 46}],
                                                 ["find_targets", {"position": "C", "max_price": 20}],
                                                 ["undo_sale", {"player": "Trey Murphy III"}], ["team_summary", {}]]},
        {"id": "D2 punt", "state": st, "steps": [["punt_scenario", {"categories": ["FT"]}], ["team_summary", {}],
                                                 ["set_category_weights", {"weights": {"FT%": 0}}], ["team_summary", {}],
                                                 ["standings", {}]]},
        {"id": "two sales", "state": st, "steps": [["record_sale", {"player": "Zion Williamson", "team": "me", "price": 12}],
                                                   ["record_sale", {"player": "Alexandre Sarr", "team": "me", "price": 13}],
                                                   ["record_sale", {"player": "Dyson Daniels", "team": "Team 3", "price": 20}],
                                                   ["team_summary", {}], ["find_targets", {}], ["standings", {}]]},
        {"id": "hypotheticals", "state": st, "steps": [
            ["find_targets", {"position": "PF", "need_categories": ["3PTM"], "punt_categories": ["FT%"], "budget_left": 40, "open_spots": 1}],
            ["find_targets", {"budget_left": 3, "open_spots": 3, "position": "C"}], ["find_targets", {"open_spots": 0}],
            ["find_targets", {"need_categories": ["STL", "BLK", "REB", "AST"], "limit": 12}],
            ["compare_players", {"names": ["Dyson Daniels", "Desmond Bane", "Nikola Jokic"]}],
            ["player_card", {"name": "jokic"}], ["player_card", {"name": "Sengun", "price": 30}],
            ["bid_check", {"name": "Jayson Tatum", "price": 500}], ["bid_check", {"name": "Nikola Jokic", "price": 5}],
            ["punt_scenario", {"categories": ["AST", "TO"], "limit": 5}]]},
        {"id": "errors", "state": st, "steps": [["player_card", {"name": "LeBron Jamesson"}]]},
        {"id": "err2", "state": st, "steps": [["find_targets", {"position": "QB"}]]},
        {"id": "err3", "state": st, "steps": [["find_targets", {"need_categories": ["dunks"]}]]},
        {"id": "err4", "state": st, "steps": [["record_sale", {"player": "Nikola Jokic", "team": "me", "price": 10}]]},
        {"id": "err5", "state": st, "steps": [["record_sale", {"player": "OG Anunoby", "team": "me", "price": 500}]]},
        {"id": "err6", "state": st, "steps": [["news_impact", {"player": "Michael Jordan", "news_text": "sprain"}]]},
        {"id": "injury history", "state": st, "steps": [["injury_history", {"player": p}] for p in
                                                        ["Tyrese Haliburton", "OG Anunoby", "Nikola Jokic", "Nobody Atall"]]},
    ]
    return out


def run_python(case):
    data = load_board_data()
    tb = Toolbox(Board(data, case["state"]))
    res = []
    for name, args in case["steps"]:
        try:
            if name == "injury_history":
                r = news.injury_history(**args)
            else:
                r = tb.call(name, args)
            res.append(json.loads(json.dumps(r, default=str)))
        except Exception as e:
            res.append({"__error__": str(e)})
    return res


def diff(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in set(a) | set(b):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: missing in {'python' if k not in a else 'js'}")
            else:
                out += diff(a[k], b[k], f"{path}.{k}")
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: length {len(a)} vs {len(b)}"]
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in diff(x, y, f"{path}[{i}]")]
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return [] if abs(a - b) <= TOL * max(1, abs(a), abs(b)) else [f"{path}: {a} vs {b}"]
    return [] if a == b else [f"{path}: {a!r} vs {b!r}"]


def main():
    data_file = HERE / "gm_data.json"
    if not data_file.exists():
        print("run python3 -m assistant.web.build_data first")
        return 1
    cs = cases()
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(cs, f)
    js = subprocess.run(["node", str(HERE / "parity_runner.js"), str(data_file), f.name], capture_output=True,
                        text=True, check=True)
    js_out = json.loads(js.stdout)
    fails = 0
    for c, jr in zip(cs, js_out):
        pr = run_python(c)
        d = diff(pr, jr)
        if d:
            fails += 1
            print(f"MISMATCH {c['id']}:")
            for x in d[:6]:
                print("   ", x)
    n_steps = sum(len(c["steps"]) for c in cs)
    print(f"{len(cs) - fails}/{len(cs)} cases match ({n_steps} tool calls)")
    print("PASS" if not fails else "FAIL")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
