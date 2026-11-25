"""What-if tools for the assistant (Elliot's use case 1).

Each tool takes plain JSON arguments and returns a JSON-able dict. Every number comes from the board
engine (assistant/engine.py), so answers match what a manager sees on the auction board.

Terms used in the outputs (same as the board):
  live      live market value: what the room's remaining money implies this player should sell for
  base      start-of-auction value with the current category weights
  my_max    highest price at which buying him still beats spending that money on an average player
  my_pts    change in MY expected roto points if I buy him at the live price (or at the price given)
  z         per-game z-score in each category (0 = average draftable player)
"""
from __future__ import annotations

from ..engine import POSITIONS, Board


def _r(x, n=2):
    return None if x is None else round(float(x), n)


def _positions(p):
    return p["pos"].split(",")


class WhatIf:
    def __init__(self, board: Board):
        self.b = board

    # ------------------------------------------------------------------ helpers
    def _player(self, name):
        p = self.b.find(name)
        if p is None:
            raise ValueError(f"No player called '{name}' on the board. Closest: {self.b.suggest(name)}")
        return p

    def _scenario(self, punt_categories=None, weights=None, budget_left=None, open_spots=None) -> Board:
        """A throwaway copy of the board with hypothetical weights / budget, leaving the real state untouched."""
        if not (punt_categories or weights or budget_left is not None or open_spots is not None):
            return self.b
        st = self.b.export_state()
        for c in punt_categories or []:
            c = self._cat(c)
            st["weights"][c] = 0
        for c, v in (weights or {}).items():
            st["weights"][self._cat(c)] = float(v)
        b = Board(self.b.data, st)
        if budget_left is not None or open_spots is not None:
            b.rebuild(me_override={"left": budget_left, "open": open_spots})
        return b

    def _cat(self, c):
        aliases = {"FG%": "FG", "FT%": "FT", "3PTM": "3PM", "3P": "3PM", "THREES": "3PM", "3S": "3PM",
                   "POINTS": "PTS", "REBOUNDS": "REB", "ASSISTS": "AST", "STEALS": "STL", "ST": "STL",
                   "BLOCKS": "BLK", "TURNOVERS": "TO", "TOV": "TO"}
        k = str(c).upper().strip()
        k = aliases.get(k, k)
        if k not in self.b.cats:
            raise ValueError(f"Unknown category '{c}'. Categories: {self.b.cats}")
        return k

    def _row(self, b: Board, p, price=None):
        me = b.me
        sold = b.sold.get(p["id"])
        row = {
            "name": p["name"], "pos": p["pos"], "team": p["team"], "exp_games": _r(p["exp_gp"], 1),
            "z": {c: _r(p["z_" + c]) for c in b.cats},
            "base": round(p["base"]), "live": round(p["live"]),
        }
        if sold:
            row["sold_to"] = b.teams[sold["team"]].name
            row["sold_price"] = sold["price"]
            return row
        row["my_max"] = b.mymax(p) if me.open > 0 else 0
        row["my_pts_at_live"] = _r(b.dme(p))
        if price is not None:
            row["price"] = price
            row["my_pts_at_price"] = _r(b.delta_me(p, price))
        return row

    # ------------------------------------------------------------------ tools
    def team_summary(self):
        """My budget, spots, roster, missing positions and projected roto points by category."""
        b, me = self.b, self.b.me
        n_sales = len(b.st["sales"])
        by_cat = {c: _r(me.pts[c], 1) for c in b.cats}
        active = [c for c in b.cats if b.st["weights"][c] > 0]
        order = sorted(active, key=lambda c: me.pts[c])
        have = {pos for r in me.roster for pos in _positions(r[0])}
        out = {
            "team": me.name, "budget_left": me.left, "open_spots": me.open, "max_single_bid": me.max_bid,
            "roster": [{"name": r[0]["name"], "pos": r[0]["pos"], "price": r[1]} for r in me.roster],
            "positions_not_covered": [x for x in POSITIONS if x not in have],
            "expected_roto_points_by_category": by_cat,
            "expected_roto_points_total": _r(me.pts["total"], 1),
            "max_points": len(active) * b.S["teams"],
            "projected_rank": 1 + sum(1 for t in b.teams if t.rp["total"] > me.rp["total"]),
            "weakest_categories": order[:3], "strongest_categories": order[::-1][:3],
            "punted_categories": [c for c in b.cats if b.st["weights"][c] == 0],
            "league_money_left": b.money, "league_spots_left": b.slots,
            "inflation_pct": round(100 * (b.inflation - 1)),
            "sales_recorded": n_sales,
        }
        if n_sales == 0:
            out["note"] = ("No sales recorded yet, so every team is projected the same; category strengths "
                           "and rank only mean something after the first sales.")
        return out

    def find_targets(self, position=None, need_categories=None, punt_categories=None, budget_left=None,
                     open_spots=None, max_price=None, limit=8):
        """Best available players for my team under the given (possibly hypothetical) situation."""
        b = self._scenario(punt_categories=punt_categories, budget_left=budget_left, open_spots=open_spots)
        me = b.me
        if me.open <= 0:
            return {"error": "No open roster spots in this scenario."}
        need = [self._cat(c) for c in need_categories or []]
        pos = position.upper().strip() if position else None
        if pos and pos not in POSITIONS + ["UTIL", "ANY"]:
            raise ValueError(f"Position must be one of {POSITIONS} (or UTIL/ANY).")
        cap = me.max_bid if max_price is None else min(int(max_price), me.max_bid)
        cands = [p for p in b.avail if not pos or pos in ("UTIL", "ANY") or pos in _positions(p)]
        cands = [p for p in cands if 0 < p["live"] and round(p["live"]) <= cap]
        rows = []
        for p in cands:
            row = self._row(b, p)
            row["need_z"] = _r(sum(p["z_" + c] for c in need)) if need else None
            row["bargain"] = row["my_max"] - row["live"]
            rows.append(row)
        if need:
            helpful = [r for r in rows if all(r["z"][c] > 0 for c in need)]
            # players who help every needed category, best roto gain first; then the rest by need_z
            helpful.sort(key=lambda r: -(r["my_pts_at_live"] or -99))
            rest = sorted([r for r in rows if r not in helpful], key=lambda r: -r["need_z"])
            rows = helpful + rest
        else:
            rows.sort(key=lambda r: -(r["my_pts_at_live"] or -99))
        note = None
        if not rows:
            note = (f"No available {pos or ''} players have a live value of ${cap} or less. With this little "
                    f"money, expect $1 end-of-draft players, who aren't priced on the board.").replace("  ", " ")
        return {
            "note": note,
            "scenario": {"position": pos or "any", "need_categories": need,
                         "punt_categories": [self._cat(c) for c in punt_categories or []],
                         "budget_left": me.left, "open_spots": me.open, "price_cap": cap,
                         "hypothetical": b is not self.b},
            "candidates_considered": len(cands),
            "targets": rows[: int(limit)],
            "how_ranked": ("players positive in every needed category first, by my roto-point gain at the live "
                           "price; then by combined z in the needed categories") if need else
                          "by my roto-point gain at the live price",
        }

    def player_card(self, name, price=None):
        """Everything the board knows about one player, plus a price ladder."""
        p = self._player(name)
        row = self._row(self.b, p, price)
        row.update({
            "past_games": p.get("hist_gp") or "rookie",
            "per_game": {k: _r(p[k], 1) for k in ["PTS", "REB", "AST", "STL", "BLK", "3PM", "TO"]},
            "fg_pct": _r(p["FGM"] / p["FGA"], 3) if p["FGA"] else None,
            "ft_pct": _r(p["FTM"] / p["FTA"], 3) if p["FTA"] else None,
        })
        if "my_max" in row and row["live"] > 0 and row["my_max"] > 0:
            steps = sorted({round(row["live"] * f) for f in (1.0, 1.1, 1.2, 1.3, 1.4)} | {row["my_max"]})
            row["price_ladder"] = [{"price": s, "my_pts": _r(self.b.delta_me(p, s))}
                                   for s in steps if s <= self.b.me.max_bid]
        return row

    def bid_check(self, name, price):
        """Should I keep bidding at this price? Same verdict rule as the board."""
        p = self._player(name)
        if p["id"] in self.b.sold:
            return self._row(self.b, p)
        price = int(price)
        mx = self.b.mymax(p)
        d = self.b.delta_me(p, price)
        if d is None:
            verdict = f"Over budget: your max single bid is ${self.b.me.max_bid}."
        elif price > mx:
            verdict = "Stop: over your max."
        elif price < mx * 0.85:
            verdict = "Undervalued: keep bidding."
        else:
            verdict = "Close to your max."
        return {"name": p["name"], "price": price, "my_max": mx, "live": round(p["live"]),
                "my_pts_at_price": _r(d), "verdict": verdict}

    def compare_players(self, names):
        return {"players": [self.player_card(n) for n in names]}

    def punt_scenario(self, categories, limit=8):
        """What changes if I punt these categories: who rises, who falls, and how my projection moves."""
        cats = [self._cat(c) for c in categories]
        nb = self._scenario(punt_categories=cats)
        moves = []
        for p in self.b.avail:
            q = nb.P[p["id"]]
            moves.append({"name": p["name"], "pos": p["pos"], "base_now": round(p["base"]),
                          "base_if_punt": round(q["base"]), "live_now": round(p["live"]),
                          "live_if_punt": round(q["live"]), "change": round(q["live"] - p["live"])})
        moves.sort(key=lambda m: -m["change"])
        return {
            "punted": cats,
            "risers": moves[: int(limit)],
            "fallers": moves[::-1][: int(limit)],
            "my_points_now": _r(self.b.me.pts["total"], 1),
            "my_points_if_punt": _r(nb.me.pts["total"], 1),
            "max_points_if_punt": (len(self.b.cats) - len(cats)) * self.b.S["teams"],
            "note": "Hypothetical only. Use set_category_weights to apply it to the board.",
        }

    def standings(self):
        b = self.b
        rows = sorted(b.teams, key=lambda t: (-t.rp["total"], -t.pts["total"]))
        return {"teams": [{"team": t.name, "me": t.i == b.st["me"], "money_left": t.left, "open_spots": t.open,
                           "roto_points": t.rp["total"], "by_category": {c: t.rp[c] for c in b.cats}}
                          for t in rows],
                "note": "Projected: open spots filled with the average player each team's money can buy."}

    def record_sale(self, player, team, price):
        return self.b.record_sale(player, team, price)

    def undo_sale(self, player):
        return self.b.undo_sale(player)

    def set_category_weights(self, weights):
        self.b.set_weights({self._cat(c): v for c, v in weights.items()})
        return {"weights": self.b.st["weights"]}


_CATS_DESC = "Categories: FG, FT, 3PM, PTS, REB, AST, STL, BLK, TO."

TOOL_SPECS = [
    {"name": "team_summary",
     "description": "My draft situation: budget left, open spots, max single bid, roster, positions not yet "
                    "covered, expected roto points per category, weakest/strongest categories, projected rank.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "find_targets",
     "description": "Rank available players for my team. Can answer hypotheticals: a position I need, "
                    "categories I'm weak in, categories to punt, a different budget or number of open spots, "
                    "or a price cap. " + _CATS_DESC,
     "input_schema": {"type": "object", "properties": {
         "position": {"type": "string", "description": "PG, SG, SF, PF, C, or UTIL/ANY"},
         "need_categories": {"type": "array", "items": {"type": "string"}},
         "punt_categories": {"type": "array", "items": {"type": "string"}},
         "budget_left": {"type": "integer", "description": "Hypothetical dollars left (default: actual)"},
         "open_spots": {"type": "integer", "description": "Hypothetical open roster spots (default: actual)"},
         "max_price": {"type": "integer"},
         "limit": {"type": "integer", "default": 8}}}},
    {"name": "player_card",
     "description": "One player's projection, category z-scores, past games, live value, my max bid, roto "
                    "gain and a price ladder. Optional price to evaluate.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "price": {"type": "integer"}},
                      "required": ["name"]}},
    {"name": "bid_check",
     "description": "Verdict for the current bid on a nominated player: keep bidding, close to max, or stop.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "price": {"type": "integer"}},
                      "required": ["name", "price"]}},
    {"name": "compare_players",
     "description": "Side-by-side player cards for 2-5 players.",
     "input_schema": {"type": "object", "properties": {"names": {"type": "array", "items": {"type": "string"}}},
                      "required": ["names"]}},
    {"name": "punt_scenario",
     "description": "Hypothetical: punt one or more categories. Shows biggest risers/fallers in value and my "
                    "projected points. Does not change the board. " + _CATS_DESC,
     "input_schema": {"type": "object", "properties": {
         "categories": {"type": "array", "items": {"type": "string"}}, "limit": {"type": "integer"}},
         "required": ["categories"]}},
    {"name": "standings",
     "description": "Projected roto standings for all teams from the auction so far.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "record_sale",
     "description": "Record that a player was sold to a team (name or index; 'me' for my team) for a price.",
     "input_schema": {"type": "object", "properties": {
         "player": {"type": "string"}, "team": {"type": "string"}, "price": {"type": "integer"}},
         "required": ["player", "team", "price"]}},
    {"name": "undo_sale",
     "description": "Remove a recorded sale.",
     "input_schema": {"type": "object", "properties": {"player": {"type": "string"}}, "required": ["player"]}},
    {"name": "set_category_weights",
     "description": "Apply category weights to the board (0 = punt, 1 = normal, up to 2). " + _CATS_DESC,
     "input_schema": {"type": "object", "properties": {"weights": {"type": "object"}}, "required": ["weights"]}},
]
