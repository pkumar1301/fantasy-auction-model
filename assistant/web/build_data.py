"""Build assistant/web/gm_data.json, the data the browser version loads.

  python3 -m assistant.web.build_data

Contents: the auction board (docs/players.js), the injury keyword tables (src/injury_types.py, in order),
the games-missed table (results/injury_absence_by_type.csv) and each board player's injury history
(precomputed with assistant.tools.news.injury_history, because the full injury CSV is 7 MB).
Re-run after the board or the injury data changes.
"""
import json
from pathlib import Path

import pandas as pd

from assistant.engine import load_board_data, norm_name
from assistant.tools import news

OUT = Path(__file__).resolve().parent / "gm_data.json"


def build():
    board = load_board_data()
    regions, types = news._keyword_tables()
    absence = pd.read_csv(news.ABSENCE_CSV)
    absence = [{"region": r.region, "type": r.type, "episodes": int(r.episodes), "median": float(r["median"]),
                "p90": float(r.p90)} for _, r in absence.iterrows()]
    hist = {}
    for p in board["players"]:
        h = news.injury_history(p["name"])
        if h.get("entries") == []:
            continue
        h.pop("player")
        hist[norm_name(p["name"])] = json.loads(json.dumps(h, default=str))
    out = {"board": board,
           "extra": {"injury_keywords": {"regions": [[k, v] for k, v in regions.items()],
                                         "types": [[k, v] for k, v in types.items()]},
                     "absence": absence, "injury_history": hist}}
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB): {len(board['players'])} players, "
          f"{len(hist)} injury histories")


if __name__ == "__main__":
    build()
