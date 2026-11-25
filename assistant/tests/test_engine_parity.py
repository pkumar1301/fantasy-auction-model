"""Parity test: the Python engine must give the same numbers as the board's JavaScript.

Run:  python3 -m assistant.tests.test_engine_parity      (needs node on PATH)
Scenarios: empty auction, a mid-auction state with sales on several teams, and a punt (FT weight 0).
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from assistant.engine import DEFAULT_DATA, Board, load_board_data

HERE = Path(__file__).resolve().parent
TOL = 1e-6

CHECK_PLAYERS = ["Trey Murphy III", "Ausar Thompson", "OG Anunoby", "Jalen Duren", "Dyson Daniels",
                 "Brook Lopez", "Naz Reid", "Tristan da Silva"]

MID_SALES = [  # (player, team, price): a plausible first 14 sales
    ("Nikola Jokic", 1, 98), ("Shai Gilgeous-Alexander", 2, 90), ("Victor Wembanyama", 3, 85),
    ("Luka Doncic", 4, 80), ("Tyrese Maxey", 0, 62), ("Anthony Edwards", 5, 60), ("Donovan Mitchell", 6, 55),
    ("Scottie Barnes", 7, 52), ("Kawhi Leonard", 8, 40), ("Jamal Murray", 0, 48), ("Jalen Johnson", 9, 47),
    ("Karl-Anthony Towns", 10, 45), ("Cooper Flagg", 11, 41), ("Chet Holmgren", 12, 44),
]


def scenarios(data):
    names = [p["name"] for p in data["players"]]
    present = [n for n in CHECK_PLAYERS if n in names]
    sales = [{"pid": names.index(n), "team": t, "price": pr} for n, t, pr in MID_SALES if n in names]
    yield "empty", {}, present
    yield "mid-auction", {"sales": sales}, present
    yield "punt FT", {"sales": sales, "weights": {**{c: 1 for c in data["settings"]["cats"]}, "FT": 0}}, present


def run_js(state, players):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(state, f)
    res = subprocess.run(["node", str(HERE / "board_engine.js"), str(DEFAULT_DATA), f.name, ",".join(players)],
                         capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def close(a, b):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= TOL * max(1, abs(a), abs(b))


def main():
    if not shutil.which("node"):
        print("SKIP: node not installed")
        return 0
    data = load_board_data()
    fails = 0
    for label, state, players in scenarios(data):
        js = run_js(state, players)
        b = Board(data, state)
        # the board computes mymax/dme for every available player; compute the same ones here
        checks = [("inflation", b.inflation, js["inflation"]), ("money", b.money, js["money"])]
        for i, t in enumerate(b.teams):
            checks += [(f"team{i}.pts", t.pts["total"], js["teams"][i]["pts"]["total"]),
                       (f"team{i}.rp", t.rp["total"], js["teams"][i]["rp"]["total"])]
        for n in players:
            p = b.find(n)
            j = js["players"][n]
            checks += [(f"{n}.live", p["live"], j["live"]), (f"{n}.base", p["base"], j["base"]),
                       (f"{n}.vw", p["vw"], j["vw"])]
            if p["id"] not in b.sold:
                checks += [(f"{n}.mymax", b.mymax(p), j["mymax"]), (f"{n}.dme", b.dme(p), j["dme"])]
        bad = [(k, a, c) for k, a, c in checks if not close(a, c)]
        fails += len(bad)
        print(f"{label:12s} {len(checks) - len(bad)}/{len(checks)} match")
        for k, a, c in bad[:10]:
            print(f"   MISMATCH {k}: python={a} js={c}")
    print("PASS" if fails == 0 else f"FAIL ({fails} mismatches)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
