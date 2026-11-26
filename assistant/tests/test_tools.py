"""Offline tests for the what-if and news tools (no API key, no network).

Run: python3 -m assistant.tests.test_tools
"""
import json
import sys
from pathlib import Path

from assistant.agent import Toolbox
from assistant.engine import Board, load_board_data
from assistant.tools import news
from assistant.tools.news import _rescale, classify_injury
from assistant.tools.whatif import WhatIf

HERE = Path(__file__).resolve().parents[1]
STATE = json.loads((HERE / "eval" / "mid_auction_state.json").read_text())
RESULTS = []


def check(cond, msg):
    RESULTS.append(bool(cond))
    print(("ok   " if cond else "FAIL ") + msg)


def raises(fn, *a, **k):
    try:
        fn(*a, **k)
        return False
    except Exception:
        return True


def main():
    data = load_board_data()
    b = Board(data, STATE)
    w = WhatIf(b)
    me = b.me

    # --- state and lookups
    check(me.left == 200 - 62 - 48 and me.open == 8, "my budget/spots from sales (Maxey $62, Murray $48)")
    check(b.find("trey murphy") and b.find("Trey Murphy III")["name"] == "Trey Murphy III", "case/suffix-insensitive name lookup")
    check(b.find("Nikola Jokić") is not None, "accented name lookup")
    check(b.find("Nobody Atall") is None, "unknown player returns None")
    check(sum(p["base"] for p in b.P) > 2799 and sum(p["base"] for p in b.P) < 2801, "start values sum to $2,800")
    check(abs(sum(p["live"] for p in b.P) - b.money) < 1e-6, "live values sum to money left in the room")

    # --- hypotheticals never change the real board
    before = json.dumps(b.export_state(), sort_keys=True)
    left0, pts0 = me.left, me.pts["total"]
    r = w.find_targets(position="PF", need_categories=["3PTM"], punt_categories=["FT%"], budget_left=40, open_spots=1)
    w.punt_scenario(["AST"])
    check(json.dumps(b.export_state(), sort_keys=True) == before and b.me.left == left0 and b.me.pts["total"] == pts0,
          "find_targets / punt_scenario leave the real state unchanged")
    check(r["scenario"]["hypothetical"] and r["scenario"]["budget_left"] == 40 and r["scenario"]["price_cap"] == 40,
          "hypothetical budget applied and reported")
    check(all("PF" in t["pos"].split(",") for t in r["targets"]), "position filter: every target is PF-eligible")
    check(all(t["live"] <= 40 for t in r["targets"]), "price cap: no target above $40 live")
    check(r["scenario"]["need_categories"] == ["3PM"] and r["scenario"]["punt_categories"] == ["FT"], "category aliases (3PTM, FT%)")
    helpful = [t for t in r["targets"] if t["z"]["3PM"] > 0]
    check(r["targets"] and r["targets"][0]["z"]["3PM"] > 0, "need category: top target is positive in 3PM")
    check(helpful == r["targets"][: len(helpful)], "players helping the need category come first")

    r2 = w.find_targets(max_price=10)
    check(all(t["live"] <= 10 for t in r2["targets"]), "max_price respected")
    r3 = w.find_targets()
    pts = [t["my_pts_at_live"] for t in r3["targets"]]
    check(pts == sorted(pts, reverse=True), "default ranking by my roto gain")
    check(all(t["name"] not in {b.P[s["pid"]]["name"] for s in b.st["sales"]} for t in r3["targets"]), "sold players never suggested")
    check(raises(w.find_targets, position="QB"), "bad position rejected")
    check(raises(w.find_targets, need_categories=["dunks"]), "bad category rejected")
    check("error" in w.find_targets(open_spots=0), "no open spots handled")

    # --- player cards and bid checks
    c = w.player_card("Trey Murphy III")
    check(c["my_max"] == b.mymax(b.find("Trey Murphy III")) and "price_ladder" in c, "player card: my_max and price ladder")
    ladder = [x["my_pts"] for x in c["price_ladder"]]
    check(ladder == sorted(ladder, reverse=True), "price ladder: paying more never gains more")
    sold = w.player_card("Nikola Jokic")
    check(sold.get("sold_to") == "Team 2" and sold.get("sold_price") == 98, "sold player card shows buyer and price")
    check(raises(w.player_card, "LeBron Jamesson"), "made-up player raises with suggestions")
    v = w.bid_check("Trey Murphy III", 5)
    check(v["verdict"].startswith("Undervalued"), "bid_check: cheap price -> keep bidding")
    v = w.bid_check("Trey Murphy III", 200)
    check(v["verdict"].startswith("Over budget"), "bid_check: above my max single bid -> over budget")
    mx = b.mymax(b.find("OG Anunoby"))
    check(w.bid_check("OG Anunoby", mx + 1)["verdict"].startswith("Stop"), "bid_check: $1 over my max -> stop")

    # --- punt scenario
    p = w.punt_scenario(["FT"])
    check(p["max_points_if_punt"] == 8 * 14, "punt: max points drops to 112")
    check(p["risers"][0]["change"] >= p["fallers"][0]["change"], "punt: risers above fallers")

    # --- summary / standings
    s = w.team_summary()
    check(s["max_points"] == 126 and 1 <= s["projected_rank"] <= 14, "team summary: points scale and rank")
    check(set(s["positions_not_covered"]) == {"SG", "SF", "PF", "C"}, "team summary: Maxey + Murray (both PG) leave SG/SF/PF/C open")
    check(len(w.standings()["teams"]) == 14, "standings: 14 teams")
    check("note" in WhatIf(Board(data)).team_summary(), "empty auction: summary warns that ranks mean nothing yet")

    # --- recording sales (on a copy)
    b2 = Board(data, STATE)
    w2 = WhatIf(b2)
    m0 = b2.money
    w2.record_sale("Trey Murphy III", "me", 46)
    check(b2.me.left == me.left - 46 and b2.money == m0 - 46, "record_sale updates my budget and the room's money")
    check(raises(w2.record_sale, "Trey Murphy III", 3, 10), "can't sell the same player twice")
    check(raises(w2.record_sale, "OG Anunoby", "me", 500), "price above max single bid rejected")
    w2.undo_sale("Trey Murphy III")
    check(b2.me.left == me.left and abs(b2.money - m0) < 1e-9, "undo_sale restores the state")
    w2.set_category_weights({"FT%": 0})
    check(b2.st["weights"]["FT"] == 0 and b2.me.pts["total"] != me.pts["total"], "set_category_weights applies a punt")

    # --- 12-team board loads too
    b12 = Board(load_board_data(HERE.parent / "docs" / "players_12.js"))
    check(b12.S["teams"] == 12 and WhatIf(b12).team_summary()["max_points"] == 108, "12-team board works")

    # --- news: classification and timelines
    cases = {
        "Left Knee; MCL sprain": ("Knee", "Sprain", None),
        "Right Achilles Tendon; Rupture": ("Achilles", "Tear / rupture", None),
        "calf strain, re-evaluated in 2 weeks": ("Lower leg", "Strain", 7.0),
        "Out 4-6 weeks with a fractured right wrist": ("Hand / wrist", "Fracture", 17.5),
        "season-ending ACL tear": ("Knee", "Tear / rupture", 82.0),
        "will miss 5 games with a sore back": ("Back / core", None, 5.0),
        "out three weeks, hamstring strain": ("Hamstring / thigh", "Strain", 10.5),
    }
    cases.update({
        "out for season": (None, None, 82.0),
        "done for the season": (None, None, 82.0),
        "will miss the rest of the season": (None, None, 82.0),   # not 'Management' via the word "rest"
        "out for the remainder of the season": (None, None, 82.0),
        "ACL tear, out for season": ("Knee", "Tear / rupture", 82.0),
        "Rest": (None, "Management", None),                        # load management still recognized
    })
    for text, (reg, typ, g) in cases.items():
        r = classify_injury(text)
        ok = r["region"] == reg and r["type"] == typ if text in ("will miss the rest of the season", "Rest") else \
            r["region"] == reg and (typ is None or r["type"] == typ)
        ok = ok and r["stated_games"] == g
        check(ok, f"classify '{text}' -> {r['region']} / {r['type']} / {r['stated_games']}")
    check(classify_injury("Ankle sprain")["historical_games_missed"]["median"] == 4.0, "ankle sprain median = 4 games")

    # --- news_impact
    imp = news.news_impact(b, "OG Anunoby", "Left calf strain, out 2-3 weeks")
    check(imp["estimate_basis"].startswith("stated") and imp["after"]["exp_games"] < imp["before"]["exp_games"],
          "news_impact: stated timeline lowers expected games")
    check(imp["after"]["live"] <= imp["before"]["live"] and imp["after"]["my_max"] <= imp["before"]["my_max"],
          "news_impact: injury lowers value and max bid")
    check(imp["after"]["z_per_game"] == imp["before"]["z_per_game"], "news_impact: injury leaves per-game quality alone")
    imp2 = news.news_impact(b, "OG Anunoby", "Right hamstring strain")
    check("historical median" in imp2["estimate_basis"] and "range_note" in imp2, "news_impact: falls back to historical median")
    imp3 = news.news_impact(b, "Trey Murphy III", minutes_change_pct=15)
    check(imp3["after"]["z_per_game"] > imp3["before"]["z_per_game"] and imp3["after"]["live"] > imp3["before"]["live"],
          "news_impact: more minutes raises value")
    imp5 = news.news_impact(b, "Jayson Tatum", "BREAKING: Tatum out for season")
    check(imp5["games_missed_estimate"] == 82 and imp5["after"]["live"] == 0, "news_impact: 'out for season' -> 82 games, $0")
    imp6 = news.news_impact(b, "OG Anunoby", "out indefinitely with a knee injury")
    check("90th percentile" in imp6["estimate_basis"] and imp6["games_missed_estimate"] > 20,
          "news_impact: 'indefinitely' uses the 90th percentile, not the median")
    imp4 = news.news_impact(b, "Jalen Duren", "torn ACL", games_missed=200)
    check(imp4["after"]["exp_games"] == 0 and imp4["after"]["live"] == 0, "news_impact: games can't go below 0")
    check("error" in news.news_impact(b, "LeBron Jamesson", "sprain"), "news_impact: unknown player")
    check(json.dumps(b.export_state(), sort_keys=True) == before, "news_impact leaves the real state unchanged")
    mx = 0
    for p in data["players"]:
        q = dict(p)
        _rescale(q, data["league"], 1.0)
        mx = max(mx, max(abs(q["z_" + c] - p["z_" + c]) for c in data["settings"]["cats"]))
    check(mx < 1e-3, f"z-score recomputation matches the pipeline (max diff {mx:.4f})")

    # --- injury history (offline dataset)
    h = news.injury_history("Tyrese Haliburton", season="2025-26")
    check(h["reports_listed_out_by_season"].get("2025-26", 0) > 50 and "Achilles" in h["longest_absences"][0]["reason"],
          "injury history: Haliburton 2025-26 Achilles absence")
    check(news.injury_history("Nobody Atall")["entries"] == [], "injury history: unknown player is empty, not an error")

    # --- round-2 regressions (Muse, 2026-11-25)
    for text in ["out through the All-Star break", "out until after the All-Star break",
                 "out through the Olympic break", "back after the break"]:
        r = news.news_impact(b, "Jayson Tatum", text)
        check(r["games_missed_estimate"] == 0 and (r["injury"] or {}).get("type") != "Fracture",
              f"R2-1 '{text}' is not a fracture")
    check(news.news_impact(b, "Jayson Tatum", "out through the All-Star break").get("needs_games_missed"),
          "R2-1 event timeline asks for games_missed instead of guessing")
    check(news.classify_injury("suffered a break in his left wrist")["type"] == "Fracture", "R2-1 a real break still counts")
    check(news.classify_injury("lower back spasms")["region"] == "Back / core", "R2-1 'back' as a body part still counts")
    for text in ["OG Anunoby Set to Miss Third Straight Game", "OG Anunoby Injury: Ruled out for Wednesday"]:
        r = news.news_impact(b, "OG Anunoby", text)
        check(r["games_missed_estimate"] == 1 and "latest_injury_report" in r["estimate_basis"], f"R2-2 '{text}' -> 1 game + check report")
    r = news.news_impact(b, "OG Anunoby", "Knicks Announce Major OG Anunoby Injury Update")
    check(r["games_missed_estimate"] == 0 and "not the same as no impact" in r["estimate_basis"], "R2-2 vague update says unknown, not no impact")
    check("already missed" in news.news_impact(b, "OG Anunoby", "calf strain")["estimate_basis"], "R2-2 median labelled as whole episode")

    def fake_get(url, **kw):
        if "news.google.com" in url:
            body = "".join(f"<item><title>OG Anunoby update {i}</title><link>https://news.google.com/rss/articles/x{i}</link>"
                           f"<pubDate>Tue, 24 Nov 2026 1{i % 10}:00:00 GMT</pubDate><source url='https://sny.tv'>SNY</source></item>"
                           for i in range(20))
        else:
            body = ("<item><title>Knicks notes</title><description>Coach on OG Anunoby and others</description>"
                    "<link>https://espn.test/k</link><pubDate>Tue, 24 Nov 2026 12:00:00 GMT</pubDate></item>")
        return type("R", (), {"text": f"<rss><channel>{body}</channel></rss>", "raise_for_status": lambda self: None})()
    real_get, real_now = news._get, news.datetime
    news._get = fake_get

    class _DT(news.datetime):
        @classmethod
        def now(cls, tz=None):
            return news.datetime(2026, 11, 25, tzinfo=tz)
    news.datetime = _DT
    try:
        r = news.rss_news("OG Anunoby", days=14)
    finally:
        news._get, news.datetime = real_get, real_now
    names_in = [x["name_in"] for x in r["items"]]
    check(r["headline_matches"] == 20 and r["summary_only_matches"] >= 1 and "summary only" in names_in and len(r["items"]) == 15,
          "R2-3 passing mentions keep a slot even with 20 headline matches")
    check(r["items"][0]["publisher_site"] == "https://sny.tv" and r["items"][0]["url_note"], "R2-4 publisher site + note on Google links")

    # --- RSS parsing and name matching (no network)
    xml = ("<rss><channel><item><title>OG Anunoby out with calf strain</title><description>&lt;p&gt;Knicks F"
           "&lt;/p&gt;</description><link>https://x.test/a</link><pubDate>Tue, 24 Nov 2026 18:00:00 GMT</pubDate>"
           "</item></channel></rss>")
    items = news._parse_rss(xml, "test")
    check(items[0]["summary"] == "Knicks F" and items[0]["published"].startswith("2026-11-24"), "RSS parse strips HTML, reads date")
    check(news._mentions("O.G. Anunoby questionable", "OG Anunoby") and not news._mentions("Anthony Davis", "Anthony Edwards"),
          "news name matching")
    gn = news.player_search_feed("OG Anunoby")
    check(gn.startswith("https://news.google.com/rss/search?q=%22OG+Anunoby%22+NBA"), "per-player news search feed URL")
    gx = ("<rss><channel><item><title>Anunoby back at practice - SNY</title><link>https://n.test</link>"
          "<pubDate>Tue, 24 Nov 2026 18:00:00 GMT</pubDate><source url='https://sny.tv'>SNY</source></item></channel></rss>")
    check(news._parse_rss(gx, "Google News search")[0]["source"] == "SNY (via Google News search)", "publisher read from news search feed")
    saved = {k: news.os.environ.pop(k, None) for k in ("TAVILY_API_KEY", "BRAVE_API_KEY", "EXA_API_KEY")}
    check("error" in news.search_news("Anunoby"), "search_news without a key returns a clear error")
    for k, v in saved.items():
        if v:
            news.os.environ[k] = v

    # --- toolbox dispatch
    tb = Toolbox(Board(data, STATE))
    names = [s["name"] for s in tb.specs]
    check(len(names) == len(set(names)) == 15, "15 tools, unique names")
    check(all(s["input_schema"]["type"] == "object" for s in tb.specs), "every tool has an object schema")
    check(raises(tb.call, "drop_table", {}), "unknown tool rejected")

    n = len(RESULTS)
    print(f"\n{sum(RESULTS)}/{n} passed")
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
