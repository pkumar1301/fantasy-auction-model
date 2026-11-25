"""Python port of the auction board's valuation engine (docs/index.html, the <script> "valuation" block).

Every number the assistant quotes comes from here, so the chat and the board never disagree.
tests/test_engine_parity.py runs the board's own JavaScript in Node on the same state and checks that
this module returns the same live prices, max bids and roto points.

Main ideas (same as the board):
  * weighted z per game  zw = sum(weight[c] * z_c); weight 0 = punt that category
  * value over replacement  vw = (zw - replacement zw) * exp_gp / 82
  * live price = remaining league money split over the best still-available players by vw
  * a team's open spots are "filled" with the average player its remaining budget can buy
  * roto points are expected category ranks against the other 13 projected teams
  * max bid = highest price at which buying the player still beats spending that money elsewhere
"""
from __future__ import annotations

import copy
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "docs" / "players.js"
STATK = ["FGM", "FGA", "FTM", "FTA", "3PM", "PTS", "REB", "AST", "STL", "BLK", "TO"]
POSITIONS = ["PG", "SG", "SF", "PF", "C"]


def load_board_data(path: Path | str = DEFAULT_DATA) -> dict:
    """Read window.FANTASY_DATA = {...} from a players*.js file."""
    s = Path(path).read_text()
    return json.loads(s[s.index("{"): s.rstrip().rstrip(";").rindex("}") + 1])


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z ]", "", s.lower().replace("-", " ").replace(".", ""))
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def phi(x: float) -> float:
    """Standard normal CDF, same polynomial approximation as the board (kept for exact parity)."""
    t = 1 / (1 + 0.2316419 * abs(x))
    d = 0.3989423 * math.exp(-x * x / 2)
    p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))))
    return 1 - p if x > 0 else p


@dataclass
class Team:
    i: int
    name: str
    roster: list = field(default_factory=list)   # list of (player dict, price)
    spent: int = 0
    left: int = 0
    open: int = 0
    max_bid: int = 0
    fill: list = field(default_factory=list)
    tot: dict = field(default_factory=dict)
    pts: dict = field(default_factory=dict)
    rp: dict = field(default_factory=dict)


class Board:
    """The auction state plus everything the board derives from it.

    state = {"me": 0, "teams": [names], "weights": {cat: w}, "sales": [{"player"|"pid", "team", "price"}]}
    This matches the board's Export file (which uses "pid"); "player" (a name) is also accepted.
    """

    def __init__(self, data: dict | None = None, state: dict | None = None):
        self.data = data or load_board_data()
        self.S = self.data["settings"]
        self.L = self.data["league"]
        self.cats = self.S["cats"]
        self.P = [dict(p, id=i) for i, p in enumerate(self.data["players"])]
        self._by_norm = {norm_name(p["name"]): p for p in self.P}
        st = copy.deepcopy(state) if state else {}
        n = self.S["teams"]
        st.setdefault("teams", [{"name": "My team" if i == 0 else f"Team {i + 1}"} for i in range(n)])
        st["teams"] = [t if isinstance(t, dict) else {"name": str(t)} for t in st["teams"]]
        st.setdefault("me", 0)
        st.setdefault("weights", {c: 1 for c in self.cats})
        st["weights"] = {c: float(st["weights"].get(c, 1)) for c in self.cats}
        st.setdefault("sales", [])
        st["sales"] = [self._norm_sale(s) for s in st["sales"]]
        self.st = st
        self.rebuild()

    # ------------------------------------------------------------------ lookups
    def find(self, name: str) -> dict | None:
        """Exact (accent/case-insensitive) match, then a unique substring or last-name match."""
        k = norm_name(name)
        if k in self._by_norm:
            return self._by_norm[k]
        hits = [p for nk, p in self._by_norm.items() if k and (k in nk or nk.split(" ")[-1] == k)]
        return hits[0] if len(hits) == 1 else None

    def suggest(self, name: str, n: int = 5) -> list[str]:
        from difflib import get_close_matches
        keys = get_close_matches(norm_name(name), list(self._by_norm), n=n, cutoff=0.6)
        return [self._by_norm[k]["name"] for k in keys]

    def team_index(self, team) -> int:
        if isinstance(team, int) or (isinstance(team, str) and team.isdigit()):
            return int(team)
        names = [norm_name(t["name"]) for t in self.st["teams"]]
        if norm_name(team) in ("me", "my team", "mine"):
            return self.st["me"]
        if norm_name(team) in names:
            return names.index(norm_name(team))
        raise ValueError(f"Unknown team '{team}'. Teams: {[t['name'] for t in self.st['teams']]}")

    def _norm_sale(self, s: dict) -> dict:
        if "pid" in s:
            pid = int(s["pid"])
        else:
            p = self.find(s["player"])
            if p is None:
                raise ValueError(f"Unknown player in sales: {s['player']}")
            pid = p["id"]
        return {"pid": pid, "team": int(s["team"]), "price": int(s["price"])}

    # ------------------------------------------------------------------ valuation (mirrors rebuild())
    def rebuild(self, me_override: dict | None = None):
        """Recompute values, prices, team totals and roto points.
        me_override = {"left": $, "open": spots} answers "what if I had $X and N spots left?" without
        inventing sales; the rest of the league stays as recorded."""
        S, L, cats, P = self.S, self.L, self.cats, self.P
        w = self.st["weights"]
        for p in P:
            p["zw"] = sum(w[c] * p["z_" + c] for c in cats)
        sold = {s["pid"]: s for s in self.st["sales"]}
        avail = [p for p in P if p["id"] not in sold]
        n_pool = S["teams"] * S["roster_spots"]
        sorted_all = sorted(P, key=lambda p: -p["zw"])
        repl = sorted_all[n_pool:n_pool + 15]
        repl_z = sum(p["zw"] for p in repl) / len(repl)
        for p in P:
            p["vw"] = (p["zw"] - repl_z) * p["exp_gp"] / 82
        teams = [Team(i=i, name=t["name"]) for i, t in enumerate(self.st["teams"])]
        for s in self.st["sales"]:
            t = teams[s["team"]]
            t.roster.append((P[s["pid"]], s["price"]))
            t.spent += s["price"]
        for t in teams:
            t.left = S["budget"] - t.spent
            t.open = S["roster_spots"] - len(t.roster)
        if me_override:
            me = teams[self.st["me"]]
            if me_override.get("left") is not None:
                me.left = int(me_override["left"])
            if me_override.get("open") is not None:
                me.open = int(me_override["open"])
        for t in teams:
            t.max_bid = t.left - (t.open - 1) if t.open > 0 else 0
        money = sum(t.left for t in teams if t.open > 0)
        slots = sum(t.open for t in teams)
        ranked = sorted(avail, key=lambda p: -p["vw"])
        top = ranked[:slots]
        V = sum(max(p["vw"], 0) for p in top)
        for p in P:
            p["live"] = 0.0
        for p in top:
            p["live"] = max(p["vw"], 0) / V * money if V > 0 else 0.0
        top_all = sorted(P, key=lambda p: -p["vw"])[:n_pool]
        V0 = sum(max(p["vw"], 0) for p in top_all)
        for p in P:
            p["base"] = 0.0
        for p in top_all:
            p["base"] = max(p["vw"], 0) / V0 * S["budget"] * S["teams"]
        self.sigma = {c: 0.5 * L["team_sigma"][c] for c in cats}
        self.avail, self.ranked, self.teams, self.money, self.slots, self.sold = avail, ranked, teams, money, slots, sold
        base_left = sum(p["base"] for p in top)
        self.inflation = money / base_left if base_left > 0 else 1.0
        self._excl = None
        for t in teams:
            t.fill = self.filler_for(t.left, t.open)
            t.tot = self.team_totals([r[0] for r in t.roster], t.fill)
        for t in teams:
            t.pts = self.roto_points(t.tot, [o.tot for o in teams if o is not t])
        self.rank_points(teams)
        for p in P:
            p.pop("dme", None)
            p.pop("mymax", None)
        return self

    @property
    def me(self) -> Team:
        return self.teams[self.st["me"]]

    def filler_for(self, budget: float, open_: int) -> list:
        if open_ <= 0:
            return []
        target = budget / open_
        bw = 4 + 0.6 * target
        pool = [p for p in self.ranked[: self.slots + 20] if p is not self._excl]
        avg = {k: 0.0 for k in ["exp_gp", "zw", *STATK]}
        W = 0.0
        for p in pool:
            wt = math.exp(-0.5 * ((p["live"] - target) / bw) ** 2)
            W += wt
            for k in avg:
                avg[k] += wt * p[k]
        for k in avg:
            avg[k] /= W
        return [avg] * open_

    def team_totals(self, players: list, fill: list) -> dict:
        left = self.S["active_slots"] * self.S["games_cap"]
        tot = {k: 0.0 for k in STATK}
        # stable sort by zw desc, same tie order as the JS (players first, then filler)
        for p in sorted([*players, *fill], key=lambda p: -p["zw"]):
            g = min(p["exp_gp"], left)
            left -= g
            for k in STATK:
                tot[k] += p[k] * g
        for k in STATK:
            tot[k] += self.L["replacement_stats"][k] * left
        return {"FG": tot["FGM"] / tot["FGA"], "FT": tot["FTM"] / tot["FTA"], "3PM": tot["3PM"], "PTS": tot["PTS"],
                "REB": tot["REB"], "AST": tot["AST"], "STL": tot["STL"], "BLK": tot["BLK"], "TO": tot["TO"]}

    def roto_points(self, mine: dict, others: list) -> dict:
        out, total = {}, 0.0
        for c in self.cats:
            s = self.sigma[c] * math.sqrt(2)
            v = 1.0
            for o in others:
                d = (o[c] - mine[c]) if c == "TO" else (mine[c] - o[c])
                v += phi(d / s)
            out[c] = v
            total += v * (1 if self.st["weights"][c] > 0 else 0)
        out["total"] = total
        return out

    def rank_points(self, teams: list):
        n = len(teams)
        for c in self.cats:
            import functools

            def cmp(a, b):
                d = (a.tot[c] - b.tot[c]) if c == "TO" else (b.tot[c] - a.tot[c])
                if abs(d) > 1e-9:
                    return -1 if d < 0 else 1
                return (b.left - a.left) or (a.i - b.i)
            for k, t in enumerate(sorted(teams, key=functools.cmp_to_key(cmp))):
                t.rp[c] = n - k
        for t in teams:
            t.rp["total"] = sum(t.rp[c] for c in self.cats if self.st["weights"][c] > 0)

    def delta_me(self, p: dict, price: float):
        """Change in MY expected roto points if I buy p at `price` (that money no longer buys an average player)."""
        me = self.me
        if me.open <= 0 or price > me.max_bid:
            return None
        self._excl = p
        fill = self.filler_for(me.left - price, me.open - 1)
        self._excl = None
        tot = self.team_totals([*[r[0] for r in me.roster], p], fill)
        return self.roto_points(tot, [o.tot for o in self.teams if o is not me])["total"] - me.pts["total"]

    def max_bid(self, p: dict) -> int:
        me = self.me
        if me.open <= 0:
            return 0
        lo, hi = 1, me.max_bid
        d1 = self.delta_me(p, 1)
        if hi < 1 or (d1 if d1 is not None else -1) <= 0:
            return 0
        dh = self.delta_me(p, hi)
        if (dh if dh is not None else -1) > 0:
            return hi
        for _ in range(9):
            mid = (lo + hi) / 2
            d = self.delta_me(p, mid)
            if d is not None and d > 0:
                lo = mid
            else:
                hi = mid
        return math.floor(lo)

    def mymax(self, p: dict) -> int:
        if "mymax" not in p:
            p["mymax"] = self.max_bid(p)
        return p["mymax"]

    def dme(self, p: dict):
        if "dme" not in p:
            p["dme"] = self.delta_me(p, p["live"])
        return p["dme"]

    # ------------------------------------------------------------------ state changes
    def record_sale(self, player: str, team, price: int) -> dict:
        p = self.find(player)
        if p is None:
            raise ValueError(f"Unknown player '{player}'. Did you mean {self.suggest(player)}?")
        if p["id"] in self.sold:
            raise ValueError(f"{p['name']} is already sold.")
        ti = self.team_index(team)
        t = self.teams[ti]
        if t.open <= 0:
            raise ValueError(f"{t.name} has no open spots.")
        if not (1 <= int(price) <= t.max_bid):
            raise ValueError(f"Price must be between $1 and ${t.max_bid} for {t.name}.")
        self.st["sales"].append({"pid": p["id"], "team": ti, "price": int(price)})
        self.rebuild()
        return {"player": p["name"], "team": t.name, "price": int(price)}

    def undo_sale(self, player: str) -> dict:
        p = self.find(player)
        if p is None or p["id"] not in self.sold:
            raise ValueError(f"No recorded sale for '{player}'.")
        self.st["sales"] = [s for s in self.st["sales"] if s["pid"] != p["id"]]
        self.rebuild()
        return {"removed": p["name"]}

    def set_weights(self, weights: dict):
        for c, v in weights.items():
            if c not in self.cats:
                raise ValueError(f"Unknown category '{c}'. Categories: {self.cats}")
            self.st["weights"][c] = float(v)
        self.rebuild()

    def export_state(self) -> dict:
        return copy.deepcopy(self.st)

    @classmethod
    def from_files(cls, data_path=None, state_path=None) -> "Board":
        data = load_board_data(data_path or DEFAULT_DATA)
        state = json.loads(Path(state_path).read_text()) if state_path else None
        return cls(data, state)
