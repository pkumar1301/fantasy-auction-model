"""News tools for the assistant (Elliot's use case 2).

Sources, cheapest first:
  injury_history       the league's official injury reports already in data/injury_reports.csv (offline)
  latest_injury_report today's official NBA injury report PDF (free, no key)
  rss_news             public NBA news feeds (free, no key)
  search_news          a news search API when the manager asks something specific (needs a key:
                       TAVILY_API_KEY, BRAVE_API_KEY or EXA_API_KEY in .env)
Then news_impact turns a piece of news into a change in the model's numbers:
  injury text -> body region + injury type (same keyword rules as src/injury_types.py)
              -> games missed (explicit "out 3 weeks" if stated, else the historical median for that
                 injury type from results/injury_absence_by_type.csv)
              -> new expected games (and optionally a minutes change for a trade or new role)
              -> re-valued on the board: base/live value, my max bid, my roto-point gain, before and after.
Every news item returned carries its date and source.
"""
from __future__ import annotations

import ast
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from pathlib import Path

import pandas as pd

from ..engine import ROOT, Board, norm_name

INJURY_CSV = ROOT / "data" / "injury_reports.csv"
ABSENCE_CSV = ROOT / "results" / "injury_absence_by_type.csv"
REPORT_URL = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_{d}_{t}.pdf"
GAMES_PER_WEEK = 3.5          # NBA teams play about 3.5 games a week
HTTP_TIMEOUT = 20
UA = {"User-Agent": "Mozilla/5.0 (fantasy-auction-model assistant; course project)"}

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
RSS_FEEDS = {
    "ESPN": "https://www.espn.com/espn/rss/nba/news",
    "CBS Sports": "https://www.cbssports.com/rss/headlines/nba/",
    "Yahoo Sports": "https://sports.yahoo.com/nba/rss.xml",
    "RotoWire": "https://www.rotowire.com/rss/news.php?sport=NBA",
}


# ---------------------------------------------------------------------- injury text -> type -> games missed
SEASON_RE = re.compile(
    r"season[- ]ending|year[- ]ending|"
    r"(?:out|done|finished|sidelined|shut down|lost|ruled out)\s+for\s+(?:the\s+)?"
    r"(?:(?:rest|remainder)\s+of\s+(?:the\s+)?)?(?:season|year|campaign)|"
    r"(?:rest|remainder|balance)\s+of\s+(?:the\s+)?(?:season|year|campaign)|"
    r"miss(?:es|ing)?\s+(?:the\s+)?(?:entire\s+|whole\s+)?(?:season|year|campaign)")
# words that look like injuries but aren't: "All-Star break", "back after the break", "is back in the lineup"
NOT_INJURY_RE = re.compile(
    r"(?:all[- ]star|olympic|winter|holiday|thanksgiving|christmas|new year'?s?|fiba|international|mid[- ]season)"
    r"\s+break|\bthe\s+break\b|"
    r"\b(?:is|be|been|was|were|are|comes?|came|coming|get|gets|got|getting|bring|brings|brought|welcome[sd]?)"
    r"\s+(?:him\s+)?back\b|\bback\s+(?:from|after|in|on|to|for|with|soon|tonight|today|next|this|into|at|practicing|playing)\b|"
    r"\bback[- ]to[- ]back\b|\bbreak(?:s|ing)?\s+(?:out|through|a record|the record)\b|\bbreakout\b")
EVENT_TIMELINE_RE = re.compile(
    r"\b(?:through|until|till|after|past|beyond)\s+(?:the\s+)?"
    r"(?:all[- ]star(?:\s+break|\s+game|\s+weekend)?|olympic\s+break|trade deadline|playoffs|new year|christmas|"
    r"thanksgiving|january|february|march|april|december|mid[- ]season|play[- ]in)")
RULED_OUT_RE = re.compile(
    r"\bruled out\b|\b(?:will|set to|expected to|going to|to)\s+miss\b|\bwon'?t play\b|\bwill sit\b|"
    r"\bsit(?:s|ting)? out\b|\blisted as out\b|\bout (?:tonight|today|monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday)\b|\b(?:doesn'?t|didn'?t|won'?t|will not) (?:practice|play|suit up)\b|\b\w+ straight game")
OPEN_ENDED_RE = re.compile(r"indefinitely|no timetable|without a timetable|no timeline|out until further notice")
@lru_cache(maxsize=None)
def _keyword_tables():
    """REGIONS and TYPES keyword tables, read from src/injury_types.py without importing it (that module
    pulls in torch), so the assistant and Project 1 always classify injuries the same way."""
    tree = ast.parse((ROOT / "src" / "injury_types.py").read_text())
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in ("REGIONS", "TYPES"):
            out[node.targets[0].id] = ast.literal_eval(node.value)
    return out["REGIONS"], out["TYPES"]


def _match(text, table):
    t = text.lower()
    for label, keys in table.items():
        if any(re.search(r"\b" + re.escape(k) if not k.startswith("-") else re.escape(k[1:]), t) for k in keys):
            return label
    return None


def _explicit_games(text):
    """Games out if the text states a timeline: 'out 2-3 weeks', 're-evaluated in 10 days', 'miss 5 games',
    'season-ending'. Returns (games, phrase) or (None, None)."""
    t = text.lower()
    m = SEASON_RE.search(t)
    if m:
        return 82.0, m.group(0)
    num = r"(\d+(?:\.\d+)?)(?:\s*(?:-|to)\s*(\d+(?:\.\d+)?))?"
    m = re.search(num + r"\s*(weeks?|days?|games?|months?)", t)
    if not m:
        words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "eight": 8, "ten": 10}
        m2 = re.search(r"\b(" + "|".join(words) + r")\s*(weeks?|days?|games?|months?)", t)
        if not m2:
            return None, None
        n, unit = words[m2.group(1)], m2.group(2)
        lo = hi = float(n)
        phrase = m2.group(0)
    else:
        lo = float(m.group(1))
        hi = float(m.group(2) or m.group(1))
        unit, phrase = m.group(3), m.group(0)
    n = (lo + hi) / 2
    games = {"w": n * GAMES_PER_WEEK, "d": n / 7 * GAMES_PER_WEEK, "g": n, "m": n * 4.3 * GAMES_PER_WEEK}[unit[0]]
    return round(games, 1), phrase


def classify_injury(text: str) -> dict:
    """Body region, injury type, and how many games that usually costs (historical median and 90th pct)."""
    regions, types = _keyword_tables()
    # remove timeline phrases before matching, so "rest of the season" doesn't read as rest (load management)
    body = NOT_INJURY_RE.sub(" ", SEASON_RE.sub(" ", text.lower()))
    region, typ = _match(body, regions), _match(body, types)
    tab = pd.read_csv(ABSENCE_CSV)
    hist = None
    if region and typ:
        r = tab[(tab.region == region) & (tab.type == typ)]
        if len(r):
            r = r.iloc[0]
            hist = {"basis": f"{region} / {typ}", "episodes": int(r.episodes), "median": float(r["median"]),
                    "p90": float(r.p90)}
    if hist is None and (region or typ):
        col, val = ("region", region) if region else ("type", typ)
        r = tab[tab[col] == val]
        if len(r):
            n = r.episodes.sum()
            med = float((r["median"] * r.episodes).sum() / n)
            hist = {"basis": f"all {val} injuries (episode-weighted)", "episodes": int(n), "median": round(med, 1),
                    "p90": float((r.p90 * r.episodes).sum() / n)}
    explicit, phrase = _explicit_games(text)
    low = text.lower()
    open_ended = bool(OPEN_ENDED_RE.search(low)) and explicit is None
    ev = EVENT_TIMELINE_RE.search(low) if explicit is None else None
    return {"region": region, "type": typ, "historical_games_missed": hist,
            "stated_timeline": phrase, "stated_games": explicit, "open_ended": open_ended,
            "unparsed_timeline": ev.group(0) if ev else None,
            "ruled_out_next_game": bool(RULED_OUT_RE.search(low))}


# ---------------------------------------------------------------------- sources
def injury_history(player: str, season: str | None = None, last_n: int = 10) -> dict:
    """Official injury-report entries for a player from the local dataset (Dec 2018 to the 2026 Finals)."""
    df = pd.read_csv(INJURY_CSV)
    key = norm_name(player)
    df = df[df.player.map(norm_name) == key]
    if season:
        df = df[df.season == season]
    if df.empty:
        return {"player": player, "entries": [], "note": "No official injury-report entries found."}
    df = df.sort_values("date")
    out = df[df.status.isin(["Out", "Doubtful"])]
    # episodes: same reason, gaps under 14 days
    eps, cur = [], None
    for r in out.itertuples():
        d = pd.Timestamp(r.date)
        if cur and r.reason == cur["reason"] and (d - cur["_last"]).days <= 14:
            cur["to"], cur["reports_out"], cur["_last"] = r.date, cur["reports_out"] + 1, d
        else:
            cur = {"reason": r.reason, "from": r.date, "to": r.date, "reports_out": 1, "_last": d}
            eps.append(cur)
    for e in eps:
        e.pop("_last")
    by_season = out.groupby("season").size().to_dict()
    return {"player": player, "source": "NBA official injury reports (data/injury_reports.csv)",
            "reports_listed_out_by_season": by_season,
            "longest_absences": sorted(eps, key=lambda e: -e["reports_out"])[:5],
            "latest_entries": df.tail(last_n)[["date", "status", "reason"]].to_dict("records"),
            "note": "Reports listed Out approximate games missed (teams file a report on game days)."}


def _get(url, **kw):
    import requests
    return requests.get(url, timeout=HTTP_TIMEOUT, headers={**UA, **kw.pop("headers", {})}, **kw)


def latest_injury_report(player: str | None = None, day: str | None = None) -> dict:
    """Today's (or a given day's) official NBA injury report, optionally filtered to one player."""
    sys.path.insert(0, str(ROOT / "src"))
    from parse_injury_reports import parse_file  # noqa: E402  (pdfplumber + pandas only)
    d = date.fromisoformat(day) if day else datetime.now(timezone(timedelta(hours=-5))).date()
    for back in range(0, 3):                      # today, else the last two days
        dd = d - timedelta(days=back)
        for h in ["08", "07", "06", "05", "04", "03", "02", "01", "12", "11"]:
            for t in (f"{h}_30PM", f"{h}_00PM", f"{h}_30AM", f"{h}_00AM"):
                try:
                    r = _get(REPORT_URL.format(d=dd.isoformat(), t=t))
                except Exception:
                    continue
                if r.status_code == 200 and r.content[:4] == b"%PDF":
                    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=tempfile.gettempdir()) as f:
                        f.write(r.content)
                    p = Path(f.name).with_name(dd.isoformat() + ".pdf")
                    Path(f.name).rename(p)
                    rows = parse_file(p)
                    if player:
                        rows = [x for x in rows if norm_name(x["player"]) == norm_name(player)]
                    return {"source": REPORT_URL.format(d=dd.isoformat(), t=t), "report_date": dd.isoformat(),
                            "report_time_et": t.replace("_", ":"), "entries": rows,
                            "note": None if rows or not player else f"{player} is not on this report (likely healthy)."}
    return {"entries": [], "note": "No official injury report found for the last 3 days (off day or offseason)."}


def _parse_rss(xml_text: str, source: str):
    items = []
    root = ET.fromstring(xml_text)
    for it in root.iter("item"):
        g = lambda tag: (it.findtext(tag) or "").strip()  # noqa: E731
        try:
            pub = parsedate_to_datetime(g("pubDate")).astimezone(timezone.utc)
        except Exception:
            pub = None
        src_el = it.find("source")
        pub_src = (src_el.text or "").strip() if src_el is not None else ""
        pub_site = src_el.get("url") if src_el is not None else None
        items.append({"source": f"{pub_src} (via {source})" if pub_src else source, "publisher_site": pub_site,
                      "title": g("title"), "summary": re.sub(r"<[^>]+>", "", g("description"))[:600],
                      "url": g("link"), "published": pub.isoformat() if pub else None,
                      "url_note": "Google News redirect link; open in a browser, or search the title on "
                                  "publisher_site" if "news.google.com" in g("link") else None})
    return items


def _mentions(text: str, player: str) -> bool:
    t, k = norm_name(text), norm_name(player)
    return bool(k) and (k in t)


def player_search_feed(player: str) -> str:
    """Google News RSS search for one player: free, no key, much better recall than general NBA feeds."""
    from urllib.parse import quote_plus
    return GOOGLE_NEWS_RSS.format(q=quote_plus(f'"{player}" NBA'))


def rss_news(player: str, days: int = 14, feeds: dict | None = None) -> dict:
    """Recent items about the player: a per-player news search feed plus the general NBA feeds.
    Items with the player's name in the headline come first; name-in-summary-only items are marked, since
    they are often about someone else (e.g. a story about his sons)."""
    feeds = feeds or {"Google News search": player_search_feed(player), **RSS_FEEDS}
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    found, errors, scanned, seen = [], {}, {}, set()
    for name, url in feeds.items():
        try:
            r = _get(url)
            r.raise_for_status()
            items = _parse_rss(r.text, name)
            scanned[name] = len(items)
            for it in items:
                if not (it["published"] is None or datetime.fromisoformat(it["published"]) >= cutoff):
                    continue
                in_title = _mentions(it["title"], player)
                if not (in_title or _mentions(it["summary"], player)):
                    continue
                key = re.sub(r"\W+", " ", it["title"].lower()).strip()[:100]  # keep digits (scores, dates)
                if key in seen:
                    continue
                seen.add(key)
                it["name_in"] = "headline" if in_title else "summary only"
                found.append(it)
        except Exception as e:
            errors[name] = type(e).__name__
    found.sort(key=lambda x: (x["name_in"] == "headline", x["published"] or ""), reverse=True)
    heads = [x for x in found if x["name_in"] == "headline"]
    summ = [x for x in found if x["name_in"] != "headline"]
    keep_s = min(3, len(summ))
    found = heads[: 15 - keep_s] + summ[:keep_s]          # up to 3 slots kept for passing mentions
    headline = len(heads)
    note = None
    if not found:
        note = "No feed items mention this player in the window. That's thin coverage, not proof of no news: try search_news."
    elif headline == 0 and summ:
        note = "He appears only in summaries, which are often about someone else. Treat as no direct news; try search_news."
    return {"player": player, "days": days, "items_scanned_by_feed": scanned, "items": found,
            "headline_matches": headline, "summary_only_matches": len(summ),
            "shown": len(found), "newest_first": True,
            "oldest_shown": min((x["published"] for x in found if x["published"]), default=None),
            "feeds_failed": errors or None, "note": note}


def search_news(query: str, days: int = 30, max_results: int = 8) -> dict:
    """News search API. Uses whichever key is set: TAVILY_API_KEY, BRAVE_API_KEY or EXA_API_KEY."""
    import requests
    if os.getenv("TAVILY_API_KEY"):
        r = requests.post("https://api.tavily.com/search", timeout=HTTP_TIMEOUT, json={
            "api_key": os.environ["TAVILY_API_KEY"], "query": query, "topic": "news", "days": days,
            "max_results": max_results})
        r.raise_for_status()
        res = [{"source": x.get("url", "").split("/")[2] if x.get("url") else None, "title": x.get("title"),
                "summary": (x.get("content") or "")[:600], "url": x.get("url"),
                "published": x.get("published_date")} for x in r.json().get("results", [])]
        provider = "Tavily"
    elif os.getenv("BRAVE_API_KEY"):
        fresh = "pd" if days <= 1 else "pw" if days <= 7 else "pm" if days <= 31 else "py"
        r = requests.get("https://api.search.brave.com/res/v1/news/search", timeout=HTTP_TIMEOUT,
                         params={"q": query, "count": max_results, "freshness": fresh},
                         headers={"X-Subscription-Token": os.environ["BRAVE_API_KEY"], "Accept": "application/json"})
        r.raise_for_status()
        res = [{"source": (x.get("meta_url") or {}).get("hostname"), "title": x.get("title"),
                "summary": (x.get("description") or "")[:600], "url": x.get("url"),
                "published": x.get("page_age") or x.get("age")} for x in r.json().get("results", [])]
        provider = "Brave"
    elif os.getenv("EXA_API_KEY"):
        start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        r = requests.post("https://api.exa.ai/search", timeout=HTTP_TIMEOUT,
                          headers={"x-api-key": os.environ["EXA_API_KEY"]},
                          json={"query": query, "numResults": max_results, "category": "news",
                                "startPublishedDate": start, "contents": {"text": {"maxCharacters": 600}}})
        r.raise_for_status()
        res = [{"source": x.get("url", "").split("/")[2] if x.get("url") else None, "title": x.get("title"),
                "summary": (x.get("text") or "")[:600], "url": x.get("url"),
                "published": x.get("publishedDate")} for x in r.json().get("results", [])]
        provider = "Exa"
    else:
        return {"error": "No search API key set (TAVILY_API_KEY, BRAVE_API_KEY or EXA_API_KEY). "
                         "Use rss_news and latest_injury_report instead."}
    return {"provider": provider, "query": query, "days": days, "items": res}


# ---------------------------------------------------------------------- news -> model numbers
def _rescale(p: dict, league: dict, k: float):
    """Scale a player's per-game line by k (minutes change) and recompute his z-scores the way
    src/build_values.py does: counting stats (x - mean)/sd, FG/FT as impact = makes - league% * attempts,
    turnovers negated."""
    for s in ["FGM", "FGA", "FTM", "FTA", "3PM", "PTS", "REB", "AST", "STL", "BLK", "TO"]:
        p[s] *= k
    zm, zs = league["z_mean"], league["z_sd"]
    raw = {"FG": p["FGM"] - league["fg_pct"] * p["FGA"], "FT": p["FTM"] - league["ft_pct"] * p["FTA"],
           "3PM": p["3PM"], "PTS": p["PTS"], "REB": p["REB"], "AST": p["AST"], "STL": p["STL"], "BLK": p["BLK"],
           "TO": -p["TO"]}
    for c, v in raw.items():
        p["z_" + c] = (v - zm[c]) / zs[c]
    p["z_pg"] = sum(p["z_" + c] for c in raw)


def news_impact(board: Board, player: str, news_text: str = "", games_missed: float | None = None,
                minutes_change_pct: float | None = None) -> dict:
    """Re-value a player after news. games_missed overrides the estimate from the text;
    minutes_change_pct (e.g. +15 after a trade to a team with an open starting role) scales his per-game line."""
    p = board.find(player)
    if p is None:
        return {"error": f"No player '{player}' on the board. Closest: {board.suggest(player)}"}
    info = classify_injury(news_text) if news_text else {}
    if games_missed is not None:
        est, basis = float(games_missed), "given"
    elif info.get("stated_games") is not None:
        est, basis = info["stated_games"], f"stated timeline ('{info['stated_timeline']}')"
    elif info.get("historical_games_missed") and info.get("open_ended"):
        est, basis = info["historical_games_missed"]["p90"], \
            f"no timetable given, so the 90th percentile for {info['historical_games_missed']['basis']}"
    elif info.get("historical_games_missed"):
        est, basis = info["historical_games_missed"]["median"], \
            (f"historical median for {info['historical_games_missed']['basis']} (a whole absence counted from its "
             f"start; games he has already missed are part of it)")
    elif info.get("open_ended"):
        est, basis = 20.0, "no timetable and no injury type recognized: assumed 20 games (about 6 weeks)"
    elif info.get("unparsed_timeline"):
        est, basis = 0.0, (f"timeline '{info['unparsed_timeline']}' can't be converted to games here; "
                           f"call again with games_missed set (count his team's games until then)")
    elif info.get("ruled_out_next_game"):
        est, basis = 1.0, ("ruled out, no injury or timeline in the text: counted as 1 game. Check "
                           "latest_injury_report for the official reason and pass it as news_text for a fuller estimate")
    else:
        est, basis = 0.0, ("no injury or timeline found in this text. That's not the same as no impact: check "
                           "latest_injury_report and pass the official reason as news_text")
    new_gp = max(0.0, p["exp_gp"] - est)

    data = {**board.data, "players": [dict(x) for x in board.data["players"]]}
    q = data["players"][p["id"]]
    q["exp_gp"] = new_gp
    if minutes_change_pct:
        _rescale(q, data["league"], 1 + minutes_change_pct / 100)
    nb = Board(data, board.export_state())

    def snap(b, x):
        out = {"exp_games": round(x["exp_gp"], 1), "base": round(x["base"]), "live": round(x["live"]),
               "z_per_game": round(x["zw"], 2)}
        if x["id"] not in b.sold and b.me.open > 0:
            out["my_max"] = b.mymax(x)
            out["my_pts_at_live"] = None if b.dme(x) is None else round(b.dme(x), 2)
        return out
    before, after = snap(board, p), snap(nb, nb.P[p["id"]])
    res = {"player": p["name"], "injury": info or None, "games_missed_estimate": round(est, 1),
           "estimate_basis": basis, "minutes_change_pct": minutes_change_pct, "before": before, "after": after,
           "change_in_live_value": after["live"] - before["live"]}
    if info.get("unparsed_timeline") and games_missed is None:
        res["needs_games_missed"] = True
    hist = (info or {}).get("historical_games_missed")
    if hist and games_missed is None and info.get("stated_games") is None and not info.get("open_ended"):
        res["range_note"] = (f"Historical median {hist['median']:.0f} games, 90th percentile {hist['p90']:.0f} "
                             f"({hist['episodes']} past episodes). Season-long games are capped by his "
                             f"projection of {p['exp_gp']:.0f}.")
    return res


TOOL_SPECS = [
    {"name": "injury_history",
     "description": "A player's official NBA injury-report history (Dec 2018 to the 2026 Finals): longest "
                    "absences, reports listed Out by season, latest entries. Offline and fast.",
     "input_schema": {"type": "object", "properties": {
         "player": {"type": "string"}, "season": {"type": "string", "description": "e.g. 2025-26"}},
         "required": ["player"]}},
    {"name": "latest_injury_report",
     "description": "Today's official NBA injury report (or a given day's), optionally for one player.",
     "input_schema": {"type": "object", "properties": {
         "player": {"type": "string"}, "day": {"type": "string", "description": "YYYY-MM-DD"}}}},
    {"name": "rss_news",
     "description": "Recent news about a player from free sources: a Google News search for him plus ESPN, CBS, "
                    "Yahoo and RotoWire feeds. Headline mentions are listed first; 'summary only' items may "
                    "be about someone else.",
     "input_schema": {"type": "object", "properties": {
         "player": {"type": "string"}, "days": {"type": "integer", "default": 14}}, "required": ["player"]}},
    {"name": "search_news",
     "description": "Search the web for news (trades, injuries, role changes). Use for specific questions "
                    "the feeds don't answer.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string"}, "days": {"type": "integer", "default": 30}}, "required": ["query"]}},
    {"name": "news_impact",
     "description": "Re-value a player after news: classifies injury text, estimates games missed (stated "
                    "timeline or historical median), optionally applies a minutes change, and returns his "
                    "value, my max bid and my roto gain before and after.",
     "input_schema": {"type": "object", "properties": {
         "player": {"type": "string"},
         "news_text": {"type": "string", "description": "The injury/news text, e.g. 'Left knee; MCL sprain, out 3 weeks'"},
         "games_missed": {"type": "number", "description": "Override the estimate if known"},
         "minutes_change_pct": {"type": "number", "description": "e.g. +15 for a bigger role, -20 for a smaller one"}},
         "required": ["player"]}},
]
