/* GM assistant tools in JavaScript, for the browser version (assistant/web).
 * A line-for-line port of assistant/engine.py, assistant/tools/whatif.py and the offline parts of
 * assistant/tools/news.py. assistant/web/parity_test.py checks it gives the same answers as the Python.
 * Works in a browser (window.GMCore) and in Node (module.exports).
 */
(function (root) {
  "use strict";
  const STATK = ["FGM", "FGA", "FTM", "FTA", "3PM", "PTS", "REB", "AST", "STL", "BLK", "TO"];
  const POSITIONS = ["PG", "SG", "SF", "PF", "C"];
  const GAMES_PER_WEEK = 3.5;

  // ---------------------------------------------------------------- helpers matching Python
  function pyRound(x, n) {
    // Python round(x, n): correctly rounded from the exact binary value, exact ties to even
    if (x === null || x === undefined) return null;
    n = n || 0;
    const exact = Math.abs(x).toFixed(Math.min(100, n + 40));       // exact decimal expansion of the double
    const dot = exact.indexOf(".");
    const tail = exact.slice(dot + 1 + n);
    let out;
    if (/^50*$/.test(tail)) {                                           // exact tie: keep the even neighbour
      const down = Number(exact.slice(0, dot + 1 + n));
      const lastDigit = Number(exact.slice(0, dot + 1 + n).replace(".", "").slice(-1));
      out = lastDigit % 2 === 0 ? down : Number((down + Math.pow(10, -n)).toFixed(n));
      out = Math.sign(x) * out;
    } else {
      out = Math.sign(x) * Number(Math.abs(x).toFixed(n));
    }
    return Object.is(out, -0) ? 0 : out;
  }
  const _r = (x, n = 2) => (x === null || x === undefined ? null : pyRound(Number(x), n));

  function normName(s) {
    s = String(s).normalize("NFKD").replace(/[^\x00-\x7f]/g, "");
    s = s.toLowerCase().replace(/-/g, " ").replace(/\./g, "").replace(/[^a-z ]/g, "");
    s = s.replace(/\b(jr|sr|ii|iii|iv)\b/g, "");
    return s.replace(/\s+/g, " ").trim();
  }

  function phi(x) {
    const t = 1 / (1 + 0.2316419 * Math.abs(x));
    const d = 0.3989423 * Math.exp(-x * x / 2);
    const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
    return x > 0 ? 1 - p : p;
  }

  // difflib.SequenceMatcher(None, a, b).ratio() and get_close_matches, enough for name suggestions
  function _longestMatch(a, alo, ahi, b, blo, bhi, b2j) {
    let besti = alo, bestj = blo, bestsize = 0, j2len = {};
    for (let i = alo; i < ahi; i++) {
      const newj2len = {};
      for (const j of (b2j[a[i]] || [])) {
        if (j < blo) continue;
        if (j >= bhi) break;
        const k = (j2len[j - 1] || 0) + 1;
        newj2len[j] = k;
        if (k > bestsize) { besti = i - k + 1; bestj = j - k + 1; bestsize = k; }
      }
      j2len = newj2len;
    }
    while (besti > alo && bestj > blo && a[besti - 1] === b[bestj - 1]) { besti--; bestj--; bestsize++; }
    while (besti + bestsize < ahi && bestj + bestsize < bhi && a[besti + bestsize] === b[bestj + bestsize]) bestsize++;
    return [besti, bestj, bestsize];
  }
  function ratio(a, b) {
    const b2j = {};
    for (let j = 0; j < b.length; j++) (b2j[b[j]] = b2j[b[j]] || []).push(j);
    let matches = 0;
    const queue = [[0, a.length, 0, b.length]];
    while (queue.length) {
      const [alo, ahi, blo, bhi] = queue.pop();
      const [i, j, k] = _longestMatch(a, alo, ahi, b, blo, bhi, b2j);
      if (k) {
        matches += k;
        if (alo < i && blo < j) queue.push([alo, i, blo, j]);
        if (i + k < ahi && j + k < bhi) queue.push([i + k, ahi, j + k, bhi]);
      }
    }
    const len = a.length + b.length;
    return len ? 2 * matches / len : 1;
  }
  function closeMatches(word, possibilities, n = 5, cutoff = 0.6) {
    const res = [];
    for (const x of possibilities) { const s = ratio(x, word); if (s >= cutoff) res.push([s, x]); }
    res.sort((p, q) => q[0] - p[0] || (q[1] < p[1] ? -1 : q[1] > p[1] ? 1 : 0));
    return res.slice(0, n).map(r => r[1]);
  }

  const deepCopy = o => JSON.parse(JSON.stringify(o));
  const fmtList = a => "[" + a.map(x => "'" + x + "'").join(", ") + "]";

  // ---------------------------------------------------------------- the board (engine.py)
  class Board {
    constructor(data, state) {
      this.data = data;
      this.S = data.settings;
      this.L = data.league;
      this.cats = this.S.cats;
      this.P = data.players.map((p, i) => Object.assign({}, p, { id: i }));
      this._byNorm = new Map();
      for (const p of this.P) this._byNorm.set(normName(p.name), p);
      const st = state ? deepCopy(state) : {};
      const n = this.S.teams;
      if (!st.teams) st.teams = Array.from({ length: n }, (_, i) => ({ name: i === 0 ? "My team" : "Team " + (i + 1) }));
      st.teams = st.teams.map(t => (typeof t === "object" ? t : { name: String(t) }));
      if (st.me === undefined) st.me = 0;
      const w = st.weights || {};
      st.weights = {};
      for (const c of this.cats) st.weights[c] = Number(w[c] === undefined ? 1 : w[c]);
      st.sales = (st.sales || []).map(s => this._normSale(s));
      this.st = st;
      this.rebuild();
    }

    find(name) {
      const k = normName(name);
      if (this._byNorm.has(k)) return this._byNorm.get(k);
      const hits = [];
      for (const [nk, p] of this._byNorm) if (k && (nk.includes(k) || nk.split(" ").pop() === k)) hits.push(p);
      return hits.length === 1 ? hits[0] : null;
    }
    suggest(name, n = 5) {
      return closeMatches(normName(name), [...this._byNorm.keys()], n, 0.6).map(k => this._byNorm.get(k).name);
    }
    teamIndex(team) {
      if (typeof team === "number" || /^\d+$/.test(String(team))) return parseInt(team, 10);
      const names = this.st.teams.map(t => normName(t.name));
      const k = normName(team);
      if (["me", "my team", "mine"].includes(k)) return this.st.me;
      if (names.includes(k)) return names.indexOf(k);
      throw new Error(`Unknown team '${team}'. Teams: ${fmtList(this.st.teams.map(t => t.name))}`);
    }
    _normSale(s) {
      let pid;
      if (s.pid !== undefined) pid = parseInt(s.pid, 10);
      else {
        const p = this.find(s.player);
        if (!p) throw new Error(`Unknown player in sales: ${s.player}`);
        pid = p.id;
      }
      return { pid, team: parseInt(s.team, 10), price: parseInt(s.price, 10) };
    }

    rebuild(meOverride) {
      const S = this.S, L = this.L, cats = this.cats, P = this.P, w = this.st.weights;
      for (const p of P) { let z = 0; for (const c of cats) z += w[c] * p["z_" + c]; p.zw = z; }
      const sold = new Map(this.st.sales.map(s => [s.pid, s]));
      const avail = P.filter(p => !sold.has(p.id));
      const nPool = S.teams * S.roster_spots;
      const sortedAll = [...P].sort((a, b) => b.zw - a.zw);
      const repl = sortedAll.slice(nPool, nPool + 15);
      const replZ = repl.reduce((s, p) => s + p.zw, 0) / repl.length;
      for (const p of P) p.vw = (p.zw - replZ) * p.exp_gp / 82;
      const teams = this.st.teams.map((t, i) => ({ i, name: t.name, roster: [], spent: 0, left: 0, open: 0, max_bid: 0,
        fill: [], tot: {}, pts: {}, rp: {} }));
      for (const s of this.st.sales) { const t = teams[s.team]; t.roster.push([P[s.pid], s.price]); t.spent += s.price; }
      for (const t of teams) { t.left = S.budget - t.spent; t.open = S.roster_spots - t.roster.length; }
      if (meOverride) {
        const me = teams[this.st.me];
        if (meOverride.left !== undefined && meOverride.left !== null) me.left = parseInt(meOverride.left, 10);
        if (meOverride.open !== undefined && meOverride.open !== null) me.open = parseInt(meOverride.open, 10);
      }
      for (const t of teams) t.max_bid = t.open > 0 ? t.left - (t.open - 1) : 0;
      const money = teams.filter(t => t.open > 0).reduce((s, t) => s + t.left, 0);
      const slots = teams.reduce((s, t) => s + t.open, 0);
      const ranked = [...avail].sort((a, b) => b.vw - a.vw);
      const top = ranked.slice(0, slots);
      const V = top.reduce((s, p) => s + Math.max(p.vw, 0), 0);
      for (const p of P) p.live = 0;
      for (const p of top) p.live = V > 0 ? Math.max(p.vw, 0) / V * money : 0;
      const topAll = [...P].sort((a, b) => b.vw - a.vw).slice(0, nPool);
      const V0 = topAll.reduce((s, p) => s + Math.max(p.vw, 0), 0);
      for (const p of P) p.base = 0;
      for (const p of topAll) p.base = Math.max(p.vw, 0) / V0 * S.budget * S.teams;
      this.sigma = {};
      for (const c of cats) this.sigma[c] = 0.5 * L.team_sigma[c];
      Object.assign(this, { avail, ranked, teams, money, slots, sold });
      const baseLeft = top.reduce((s, p) => s + p.base, 0);
      this.inflation = baseLeft > 0 ? money / baseLeft : 1.0;
      this._excl = null;
      for (const t of teams) { t.fill = this.fillerFor(t.left, t.open); t.tot = this.teamTotals(t.roster.map(r => r[0]), t.fill); }
      for (const t of teams) t.pts = this.rotoPoints(t.tot, teams.filter(o => o !== t).map(o => o.tot));
      this.rankPoints(teams);
      for (const p of P) { delete p.dme; delete p.mymax; }
      return this;
    }
    get me() { return this.teams[this.st.me]; }

    fillerFor(budget, open) {
      if (open <= 0) return [];
      const target = budget / open, bw = 4 + 0.6 * target;
      const pool = this.ranked.slice(0, this.slots + 20).filter(p => p !== this._excl);
      const keys = ["exp_gp", "zw", ...STATK];
      const avg = {}; for (const k of keys) avg[k] = 0;
      let W = 0;
      for (const p of pool) {
        const wt = Math.exp(-0.5 * Math.pow((p.live - target) / bw, 2));
        W += wt;
        for (const k of keys) avg[k] += wt * p[k];
      }
      for (const k of keys) avg[k] /= W;
      return Array(open).fill(avg);
    }
    teamTotals(players, fill) {
      let left = this.S.active_slots * this.S.games_cap;
      const tot = {}; for (const k of STATK) tot[k] = 0;
      for (const p of [...players, ...fill].sort((a, b) => b.zw - a.zw)) {
        const g = Math.min(p.exp_gp, left);
        left -= g;
        for (const k of STATK) tot[k] += p[k] * g;
      }
      for (const k of STATK) tot[k] += this.L.replacement_stats[k] * left;
      return { FG: tot.FGM / tot.FGA, FT: tot.FTM / tot.FTA, "3PM": tot["3PM"], PTS: tot.PTS, REB: tot.REB,
        AST: tot.AST, STL: tot.STL, BLK: tot.BLK, TO: tot.TO };
    }
    rotoPoints(mine, others) {
      const out = {}; let total = 0;
      for (const c of this.cats) {
        const s = this.sigma[c] * Math.sqrt(2);
        let v = 1;
        for (const o of others) { const d = c === "TO" ? o[c] - mine[c] : mine[c] - o[c]; v += phi(d / s); }
        out[c] = v;
        total += v * (this.st.weights[c] > 0 ? 1 : 0);
      }
      out.total = total;
      return out;
    }
    rankPoints(teams) {
      const n = teams.length;
      for (const c of this.cats) {
        const sorted = [...teams].sort((a, b) => {
          const d = c === "TO" ? a.tot[c] - b.tot[c] : b.tot[c] - a.tot[c];
          if (Math.abs(d) > 1e-9) return d < 0 ? -1 : 1;
          return (b.left - a.left) || (a.i - b.i);
        });
        sorted.forEach((t, k) => { t.rp[c] = n - k; });
      }
      for (const t of teams) t.rp.total = this.cats.filter(c => this.st.weights[c] > 0).reduce((s, c) => s + t.rp[c], 0);
    }
    deltaMe(p, price) {
      const me = this.me;
      if (me.open <= 0 || price > me.max_bid) return null;
      this._excl = p;
      const fill = this.fillerFor(me.left - price, me.open - 1);
      this._excl = null;
      const tot = this.teamTotals([...me.roster.map(r => r[0]), p], fill);
      return this.rotoPoints(tot, this.teams.filter(o => o !== me).map(o => o.tot)).total - me.pts.total;
    }
    maxBid(p) {
      const me = this.me;
      if (me.open <= 0) return 0;
      let lo = 1, hi = me.max_bid;
      const d1 = this.deltaMe(p, 1);
      if (hi < 1 || (d1 !== null ? d1 : -1) <= 0) return 0;
      const dh = this.deltaMe(p, hi);
      if ((dh !== null ? dh : -1) > 0) return hi;
      for (let k = 0; k < 9; k++) {
        const mid = (lo + hi) / 2;
        const d = this.deltaMe(p, mid);
        if (d !== null && d > 0) lo = mid; else hi = mid;
      }
      return Math.floor(lo);
    }
    mymax(p) { if (!("mymax" in p)) p.mymax = this.maxBid(p); return p.mymax; }
    dme(p) { if (!("dme" in p)) p.dme = this.deltaMe(p, p.live); return p.dme; }

    recordSale(player, team, price) {
      const p = this.find(player);
      if (!p) throw new Error(`Unknown player '${player}'. Did you mean ${fmtList(this.suggest(player))}?`);
      if (this.sold.has(p.id)) throw new Error(`${p.name} is already sold.`);
      const ti = this.teamIndex(team), t = this.teams[ti];
      if (t.open <= 0) throw new Error(`${t.name} has no open spots.`);
      price = parseInt(price, 10);
      if (!(price >= 1 && price <= t.max_bid)) throw new Error(`Price must be between $1 and $${t.max_bid} for ${t.name}.`);
      this.st.sales.push({ pid: p.id, team: ti, price });
      this.rebuild();
      return { player: p.name, team: t.name, price };
    }
    undoSale(player) {
      const p = this.find(player);
      if (!p || !this.sold.has(p.id)) throw new Error(`No recorded sale for '${player}'.`);
      this.st.sales = this.st.sales.filter(s => s.pid !== p.id);
      this.rebuild();
      return { removed: p.name };
    }
    setWeights(weights) {
      for (const [c, v] of Object.entries(weights)) {
        if (!this.cats.includes(c)) throw new Error(`Unknown category '${c}'. Categories: ${fmtList(this.cats)}`);
        this.st.weights[c] = Number(v);
      }
      this.rebuild();
    }
    exportState() { return deepCopy(this.st); }
  }

  // ---------------------------------------------------------------- what-if tools (whatif.py)
  const positionsOf = p => p.pos.split(",");
  const CAT_ALIASES = { "FG%": "FG", "FT%": "FT", "3PTM": "3PM", "3P": "3PM", "THREES": "3PM", "3S": "3PM",
    POINTS: "PTS", REBOUNDS: "REB", ASSISTS: "AST", STEALS: "STL", ST: "STL", BLOCKS: "BLK", TURNOVERS: "TO", TOV: "TO" };

  class WhatIf {
    constructor(board) { this.b = board; }
    _player(name) {
      const p = this.b.find(name);
      if (!p) throw new Error(`No player called '${name}' on the board. Closest: ${fmtList(this.b.suggest(name))}`);
      return p;
    }
    _cat(c) {
      let k = String(c).toUpperCase().trim();
      k = CAT_ALIASES[k] || k;
      if (!this.b.cats.includes(k)) throw new Error(`Unknown category '${c}'. Categories: ${fmtList(this.b.cats)}`);
      return k;
    }
    _scenario({ punt_categories, weights, budget_left, open_spots } = {}) {
      const has = v => v !== undefined && v !== null;
      if (!((punt_categories && punt_categories.length) || (weights && Object.keys(weights).length) || has(budget_left) || has(open_spots))) return this.b;
      const st = this.b.exportState();
      for (const c of punt_categories || []) st.weights[this._cat(c)] = 0;
      for (const [c, v] of Object.entries(weights || {})) st.weights[this._cat(c)] = Number(v);
      const b = new Board(this.b.data, st);
      if (has(budget_left) || has(open_spots)) b.rebuild({ left: budget_left, open: open_spots });
      return b;
    }
    _row(b, p, price) {
      const me = b.me, sold = b.sold.get(p.id);
      const z = {}; for (const c of b.cats) z[c] = _r(p["z_" + c]);
      const row = { name: p.name, pos: p.pos, team: p.team, exp_games: _r(p.exp_gp, 1), z,
        base: pyRound(p.base), live: pyRound(p.live) };
      if (sold) { row.sold_to = b.teams[sold.team].name; row.sold_price = sold.price; return row; }
      row.my_max = me.open > 0 ? b.mymax(p) : 0;
      row.my_pts_at_live = _r(b.dme(p));
      if (price !== undefined && price !== null) { row.price = price; row.my_pts_at_price = _r(b.deltaMe(p, price)); }
      return row;
    }

    team_summary() {
      const b = this.b, me = b.me, nSales = b.st.sales.length;
      const byCat = {}; for (const c of b.cats) byCat[c] = _r(me.pts[c], 1);
      const active = b.cats.filter(c => b.st.weights[c] > 0);
      const order = [...active].sort((x, y) => me.pts[x] - me.pts[y]);
      const have = new Set(); for (const r of me.roster) for (const ps of positionsOf(r[0])) have.add(ps);
      const out = { team: me.name, spent: me.spent, budget_left: me.left, open_spots: me.open, max_single_bid: me.max_bid,
        roster: me.roster.map(r => ({ name: r[0].name, pos: r[0].pos, price: r[1] })),
        positions_not_covered: POSITIONS.filter(x => !have.has(x)),
        expected_roto_points_by_category: byCat, expected_roto_points_total: _r(me.pts.total, 1),
        max_points: active.length * b.S.teams,
        projected_rank: 1 + b.teams.filter(t => t.rp.total > me.rp.total).length,
        weakest_categories: order.slice(0, 3), strongest_categories: [...order].reverse().slice(0, 3),
        punted_categories: b.cats.filter(c => b.st.weights[c] === 0),
        league_money_left: b.money, league_spots_left: b.slots, inflation_pct: pyRound(100 * (b.inflation - 1)),
        sales_recorded: nSales };
      if (nSales === 0) out.note = "No sales recorded yet, so every team is projected the same; category strengths and rank only mean something after the first sales.";
      return out;
    }
    find_targets({ position, need_categories, punt_categories, budget_left, open_spots, max_price, limit = 8 } = {}) {
      const b = this._scenario({ punt_categories, budget_left, open_spots }), me = b.me;
      if (me.open <= 0) return { error: "No open roster spots in this scenario." };
      const need = (need_categories || []).map(c => this._cat(c));
      const pos = position ? String(position).toUpperCase().trim() : null;
      if (pos && ![...POSITIONS, "UTIL", "ANY"].includes(pos)) throw new Error(`Position must be one of ${fmtList(POSITIONS)} (or UTIL/ANY).`);
      const cap = (max_price === undefined || max_price === null) ? me.max_bid : Math.min(parseInt(max_price, 10), me.max_bid);
      let cands = b.avail.filter(p => !pos || pos === "UTIL" || pos === "ANY" || positionsOf(p).includes(pos));
      cands = cands.filter(p => 0 < p.live && pyRound(p.live) <= cap);
      let rows = cands.map(p => {
        const row = this._row(b, p);
        row.need_z = need.length ? _r(need.reduce((s, c) => s + p["z_" + c], 0)) : null;
        row.bargain = row.my_max - row.live;
        return row;
      });
      const key = r => -(r.my_pts_at_live || -99);
      if (need.length) {
        const helpful = rows.filter(r => need.every(c => r.z[c] > 0)).sort((x, y) => key(x) - key(y));
        const rest = rows.filter(r => !helpful.includes(r)).sort((x, y) => (-x.need_z) - (-y.need_z));
        rows = [...helpful, ...rest];
      } else rows.sort((x, y) => key(x) - key(y));
      let note = null;
      if (!rows.length) note = `No available ${pos || ""} players have a live value of $${cap} or less. With this little money, expect $1 end-of-draft players, who aren't priced on the board.`.replace("  ", " ");
      return { note,
        scenario: { position: pos || "any", need_categories: need, punt_categories: (punt_categories || []).map(c => this._cat(c)),
          budget_left: me.left, open_spots: me.open, price_cap: cap, hypothetical: b !== this.b },
        candidates_considered: cands.length, targets: rows.slice(0, parseInt(limit, 10)),
        how_ranked: need.length ? "players positive in every needed category first, by my roto-point gain at the live price; then by combined z in the needed categories"
          : "by my roto-point gain at the live price" };
    }
    player_card({ name, price } = {}) {
      const p = this._player(name);
      const row = this._row(this.b, p, price);
      const pg = {}; for (const k of ["PTS", "REB", "AST", "STL", "BLK", "3PM", "TO"]) pg[k] = _r(p[k], 1);
      Object.assign(row, { past_games: (p.hist_gp && p.hist_gp.length) ? p.hist_gp : "rookie", per_game: pg,
        fg_pct: p.FGA ? _r(p.FGM / p.FGA, 3) : null, ft_pct: p.FTA ? _r(p.FTM / p.FTA, 3) : null });
      if ("my_max" in row && row.live > 0 && row.my_max > 0) {
        const steps = [...new Set([1.0, 1.1, 1.2, 1.3, 1.4].map(f => pyRound(row.live * f)).concat([row.my_max]))].sort((x, y) => x - y);
        row.price_ladder = steps.filter(s => s <= this.b.me.max_bid).map(s => ({ price: s, my_pts: _r(this.b.deltaMe(p, s)) }));
      }
      return row;
    }
    bid_check({ name, price } = {}) {
      const p = this._player(name);
      if (this.b.sold.has(p.id)) return this._row(this.b, p);
      price = parseInt(price, 10);
      const mx = this.b.mymax(p), d = this.b.deltaMe(p, price);
      let verdict;
      if (d === null) verdict = `Over budget: your max single bid is $${this.b.me.max_bid}.`;
      else if (price > mx) verdict = "Stop: over your max.";
      else if (price < mx * 0.85) verdict = "Undervalued: keep bidding.";
      else verdict = "Close to your max.";
      const me = this.b.me;
      const out = { name: p.name, price, my_max: mx, live: pyRound(p.live), my_pts_at_price: _r(d), verdict };
      if (d !== null) Object.assign(out, { budget_after: me.left - price, spots_after: me.open - 1,
        max_single_bid_after: me.open > 1 ? (me.left - price) - (me.open - 2) : 0, below_my_max_by: mx - price });
      return out;
    }
    compare_players({ names } = {}) { return { players: (names || []).map(n => this.player_card({ name: n })) }; }
    punt_scenario({ categories, limit = 8 } = {}) {
      const cats = (categories || []).map(c => this._cat(c));
      const nb = this._scenario({ punt_categories: cats });
      const moves = this.b.avail.map(p => {
        const q = nb.P[p.id];
        return { name: p.name, pos: p.pos, base_now: pyRound(p.base), base_if_punt: pyRound(q.base), live_now: pyRound(p.live),
          live_if_punt: pyRound(q.live), change: pyRound(q.live - p.live) };
      });
      moves.sort((x, y) => (-x.change) - (-y.change));
      return { punted: cats, risers: moves.slice(0, limit), fallers: [...moves].reverse().slice(0, limit),
        my_points_now: _r(this.b.me.pts.total, 1), my_points_if_punt: _r(nb.me.pts.total, 1),
        max_points_if_punt: (this.b.cats.length - cats.length) * this.b.S.teams,
        note: "Hypothetical only. Use set_category_weights to apply it to the board." };
    }
    standings() {
      const b = this.b;
      const rows = [...b.teams].sort((x, y) => (-x.rp.total) - (-y.rp.total) || (-x.pts.total) - (-y.pts.total));
      return { teams: rows.map(t => { const bc = {}; for (const c of b.cats) bc[c] = t.rp[c];
        return { team: t.name, me: t.i === b.st.me, money_left: t.left, open_spots: t.open, roto_points: t.rp.total, by_category: bc }; }),
        note: "Projected: open spots filled with the average player each team's money can buy." };
    }
    record_sale({ player, team, price } = {}) { return this.b.recordSale(player, team, price); }
    undo_sale({ player } = {}) { return this.b.undoSale(player); }
    set_category_weights({ weights } = {}) {
      const w = {}; for (const [c, v] of Object.entries(weights || {})) w[this._cat(c)] = v;
      this.b.setWeights(w);
      return { weights: this.b.st.weights };
    }
  }

  // ---------------------------------------------------------------- offline news tools (news.py)
  const SEASON_RE = new RegExp(
    "season[- ]ending|year[- ]ending|" +
    "(?:out|done|finished|sidelined|shut down|lost|ruled out)\\s+for\\s+(?:the\\s+)?" +
    "(?:(?:rest|remainder)\\s+of\\s+(?:the\\s+)?)?(?:season|year|campaign)|" +
    "(?:rest|remainder|balance)\\s+of\\s+(?:the\\s+)?(?:season|year|campaign)|" +
    "miss(?:es|ing)?\\s+(?:the\\s+)?(?:entire\\s+|whole\\s+)?(?:season|year|campaign)");
  const NOT_INJURY_RE = new RegExp(
    "(?:all[- ]star|olympic|winter|holiday|thanksgiving|christmas|new year'?s?|fiba|international|mid[- ]season)" +
    "\\s+break|\\bthe\\s+break\\b|" +
    "\\b(?:is|be|been|was|were|are|comes?|came|coming|get|gets|got|getting|bring|brings|brought|welcome[sd]?)" +
    "\\s+(?:him\\s+)?back\\b|\\bback\\s+(?:from|after|in|on|to|for|with|soon|tonight|today|next|this|into|at|practicing|playing)\\b|" +
    "\\bback[- ]to[- ]back\\b|\\bbreak(?:s|ing)?\\s+(?:out|through|a record|the record)\\b|\\bbreakout\\b", "g");
  const EVENT_TIMELINE_RE = new RegExp(
    "\\b(?:through|until|till|after|past|beyond)\\s+(?:the\\s+)?" +
    "(?:all[- ]star(?:\\s+break|\\s+game|\\s+weekend)?|olympic\\s+break|trade deadline|playoffs|new year|christmas|" +
    "thanksgiving|january|february|march|april|december|mid[- ]season|play[- ]in)");
  const RULED_OUT_RE = new RegExp(
    "\\bruled out\\b|\\b(?:will|set to|expected to|going to|to)\\s+miss\\b|\\bwon'?t play\\b|\\bwill sit\\b|" +
    "\\bsit(?:s|ting)? out\\b|\\blisted as out\\b|\\bout (?:tonight|today|monday|tuesday|wednesday|thursday|friday|" +
    "saturday|sunday)\\b|\\b(?:doesn'?t|didn'?t|won'?t|will not) (?:practice|play|suit up)\\b|\\b\\w+ straight game");
  const SEASON_RE_G = new RegExp(SEASON_RE.source, "g");
  const OPEN_ENDED_RE = /indefinitely|no timetable|without a timetable|no timeline|out until further notice/;
  const reEsc = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  function matchTable(text, table) {
    const t = text.toLowerCase();
    for (const [label, keys] of table) {
      if (keys.some(k => new RegExp(k.startsWith("-") ? reEsc(k.slice(1)) : "\\b" + reEsc(k)).test(t))) return label;
    }
    return null;
  }
  function explicitGames(text) {
    const t = text.toLowerCase();
    const m0 = SEASON_RE.exec(t);
    if (m0) return [82.0, m0[0]];
    const m = /(\d+(?:\.\d+)?)(?:\s*(?:-|to)\s*(\d+(?:\.\d+)?))?\s*(weeks?|days?|games?|months?)/.exec(t);
    let lo, hi, unit, phrase;
    if (!m) {
      const words = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, eight: 8, ten: 10 };
      const m2 = new RegExp("\\b(" + Object.keys(words).join("|") + ")\\s*(weeks?|days?|games?|months?)").exec(t);
      if (!m2) return [null, null];
      lo = hi = words[m2[1]]; unit = m2[2]; phrase = m2[0];
    } else { lo = parseFloat(m[1]); hi = parseFloat(m[2] || m[1]); unit = m[3]; phrase = m[0]; }
    const n = (lo + hi) / 2;
    const games = { w: n * GAMES_PER_WEEK, d: n / 7 * GAMES_PER_WEEK, g: n, m: n * 4.3 * GAMES_PER_WEEK }[unit[0]];
    return [pyRound(games, 1), phrase];
  }

  class News {
    constructor(board, extra, opts = {}) {
      this.b = board;
      this.regions = extra.injury_keywords.regions;      // [[label, [keywords]], ...] in file order
      this.types = extra.injury_keywords.types;
      this.absence = extra.absence;                      // [{region, type, episodes, median, p90}]
      this.history = extra.injury_history;               // {normName: injury_history result}
      this.web = !!opts.web;
    }
    classify_injury(text) {
      const body = text.toLowerCase().replace(SEASON_RE_G, " ").replace(NOT_INJURY_RE, " ");
      const region = matchTable(body, this.regions), typ = matchTable(body, this.types);
      let hist = null;
      if (region && typ) {
        const r = this.absence.find(a => a.region === region && a.type === typ);
        if (r) hist = { basis: `${region} / ${typ}`, episodes: r.episodes, median: r.median, p90: r.p90 };
      }
      if (!hist && (region || typ)) {
        const [col, val] = region ? ["region", region] : ["type", typ];
        const rs = this.absence.filter(a => a[col] === val);
        if (rs.length) {
          const n = rs.reduce((s, a) => s + a.episodes, 0);
          const med = rs.reduce((s, a) => s + a.median * a.episodes, 0) / n;
          hist = { basis: `all ${val} injuries (episode-weighted)`, episodes: n, median: pyRound(med, 1),
            p90: rs.reduce((s, a) => s + a.p90 * a.episodes, 0) / n };
        }
      }
      const [explicit, phrase] = explicitGames(text);
      const low = text.toLowerCase();
      const openEnded = OPEN_ENDED_RE.test(low) && explicit === null;
      const ev = explicit === null ? EVENT_TIMELINE_RE.exec(low) : null;
      return { region, type: typ, historical_games_missed: hist, stated_timeline: phrase, stated_games: explicit,
        open_ended: openEnded, unparsed_timeline: ev ? ev[0] : null, ruled_out_next_game: RULED_OUT_RE.test(low) };
    }
    injury_history({ player } = {}) {
      const p = this.b.find(player);
      const key = normName(p ? p.name : player);
      const h = this.history[key];
      if (!h) return { player, entries: [], note: "No official injury-report entries found." };
      return Object.assign({ player }, h);
    }
    news_impact({ player, news_text = "", games_missed = null, minutes_change_pct = null } = {}) {
      const board = this.b, p = board.find(player);
      if (!p) return { error: `No player '${player}' on the board. Closest: ${fmtList(board.suggest(player))}` };
      const info = news_text ? this.classify_injury(news_text) : {};
      const report = this.web ? "ask the manager for the official injury reason (from the NBA injury report) and pass it as news_text"
        : "check latest_injury_report and pass the official reason as news_text";
      let est, basis;
      const has = v => v !== undefined && v !== null;
      if (has(games_missed)) { est = Number(games_missed); basis = "given"; }
      else if (has(info.stated_games)) { est = info.stated_games; basis = `stated timeline ('${info.stated_timeline}')`; }
      else if (info.historical_games_missed && info.open_ended) { est = info.historical_games_missed.p90;
        basis = `no timetable given, so the 90th percentile for ${info.historical_games_missed.basis}`; }
      else if (info.historical_games_missed) { est = info.historical_games_missed.median;
        basis = `historical median for ${info.historical_games_missed.basis} (a whole absence counted from its start; games he has already missed are part of it)`; }
      else if (info.open_ended) { est = 20.0; basis = "no timetable and no injury type recognized: assumed 20 games (about 6 weeks)"; }
      else if (info.unparsed_timeline) { est = 0.0; basis = `timeline '${info.unparsed_timeline}' can't be converted to games here; call again with games_missed set (count his team's games until then)`; }
      else if (info.ruled_out_next_game) { est = 1.0; basis = this.web
        ? "ruled out, no injury or timeline in the text: counted as 1 game. For a fuller estimate, " + report
        : "ruled out, no injury or timeline in the text: counted as 1 game. Check latest_injury_report for the official reason and pass it as news_text for a fuller estimate"; }
      else { est = 0.0; basis = "no injury or timeline found in this text. That's not the same as no impact: " + report; }
      const newGp = Math.max(0.0, p.exp_gp - est);
      const data = Object.assign({}, board.data, { players: board.data.players.map(x => Object.assign({}, x)) });
      const q = data.players[p.id];
      q.exp_gp = newGp;
      if (minutes_change_pct) rescale(q, data.league, 1 + minutes_change_pct / 100);
      const nb = new Board(data, board.exportState());
      const snap = (b, x) => {
        const out = { exp_games: pyRound(x.exp_gp, 1), base: pyRound(x.base), live: pyRound(x.live), z_per_game: pyRound(x.zw, 2) };
        if (!b.sold.has(x.id) && b.me.open > 0) { out.my_max = b.mymax(x); const d = b.dme(x); out.my_pts_at_live = d === null ? null : pyRound(d, 2); }
        return out;
      };
      const before = snap(board, p), after = snap(nb, nb.P[p.id]);
      const res = { player: p.name, injury: Object.keys(info).length ? info : null, games_missed_estimate: pyRound(est, 1),
        estimate_basis: basis, minutes_change_pct, before, after, change_in_live_value: after.live - before.live };
      if (info.unparsed_timeline && !has(games_missed)) res.needs_games_missed = true;
      const hist = info.historical_games_missed;
      if (hist && !has(games_missed) && !has(info.stated_games) && !info.open_ended)
        res.range_note = `Historical median ${pyRound(hist.median, 0).toFixed(0)} games, 90th percentile ${pyRound(hist.p90, 0).toFixed(0)} (${hist.episodes} past episodes). Season-long games are capped by his projection of ${pyRound(p.exp_gp, 0).toFixed(0)}.`;
      return res;
    }
  }
  function rescale(p, league, k) {
    for (const s of STATK) p[s] *= k;
    const zm = league.z_mean, zs = league.z_sd;
    const raw = { FG: p.FGM - league.fg_pct * p.FGA, FT: p.FTM - league.ft_pct * p.FTA, "3PM": p["3PM"], PTS: p.PTS,
      REB: p.REB, AST: p.AST, STL: p.STL, BLK: p.BLK, TO: -p.TO };
    let zpg = 0;
    for (const [c, v] of Object.entries(raw)) { p["z_" + c] = (v - zm[c]) / zs[c]; zpg += p["z_" + c]; }
    p.z_pg = zpg;
  }

  // ---------------------------------------------------------------- toolbox
  const CATS_DESC = "Categories: FG, FT, 3PM, PTS, REB, AST, STL, BLK, TO.";
  const TOOL_SPECS = [
    { name: "team_summary", description: "My draft situation: budget left, open spots, max single bid, roster, positions not yet covered, expected roto points per category, weakest/strongest categories, projected rank.",
      inputSchema: { type: "object", properties: {} } },
    { name: "find_targets", description: "Rank available players for my team by my roto-point gain at the live price (which already accounts for my weak categories). Can answer hypotheticals: a position I need, categories the manager says they need (need_categories: only when asked), categories to punt, a different budget or number of open spots, or a price cap. " + CATS_DESC,
      inputSchema: { type: "object", properties: { position: { type: "string", description: "PG, SG, SF, PF, C, or UTIL/ANY" },
        need_categories: { type: "array", items: { type: "string" } }, punt_categories: { type: "array", items: { type: "string" } },
        budget_left: { type: "integer", description: "Hypothetical dollars left (default: actual)" },
        open_spots: { type: "integer", description: "Hypothetical open roster spots (default: actual)" },
        max_price: { type: "integer" }, limit: { type: "integer", default: 8 } } } },
    { name: "player_card", description: "One player's projection, category z-scores, past games, live value, my max bid, roto gain and a price ladder. Optional price to evaluate.",
      inputSchema: { type: "object", properties: { name: { type: "string" }, price: { type: "integer" } }, required: ["name"] } },
    { name: "bid_check", description: "Verdict for the current bid on a nominated player: keep bidding, close to max, or stop.",
      inputSchema: { type: "object", properties: { name: { type: "string" }, price: { type: "integer" } }, required: ["name", "price"] } },
    { name: "compare_players", description: "Side-by-side player cards for 2-5 players.",
      inputSchema: { type: "object", properties: { names: { type: "array", items: { type: "string" } } }, required: ["names"] } },
    { name: "punt_scenario", description: "Hypothetical: punt one or more categories. Shows biggest risers/fallers in value and my projected points. Does not change the board. " + CATS_DESC,
      inputSchema: { type: "object", properties: { categories: { type: "array", items: { type: "string" } }, limit: { type: "integer" } }, required: ["categories"] } },
    { name: "standings", description: "Projected roto standings for all teams from the auction so far.", inputSchema: { type: "object", properties: {} } },
    { name: "record_sale", description: "Record that a player was sold to a team (name or index; 'me' for my team) for a price.",
      inputSchema: { type: "object", properties: { player: { type: "string" }, team: { type: "string" }, price: { type: "integer" } }, required: ["player", "team", "price"] } },
    { name: "undo_sale", description: "Remove a recorded sale.", inputSchema: { type: "object", properties: { player: { type: "string" } }, required: ["player"] } },
    { name: "set_category_weights", description: "Apply category weights to the board (0 = punt, 1 = normal, up to 2). " + CATS_DESC,
      inputSchema: { type: "object", properties: { weights: { type: "object" } }, required: ["weights"] } },
    { name: "injury_history", description: "A player's official NBA injury-report history (Dec 2018 to the 2026 Finals): longest absences, reports listed Out by season, latest entries.",
      inputSchema: { type: "object", properties: { player: { type: "string" } }, required: ["player"] } },
    { name: "news_impact", description: "Re-value a player after news: classifies injury text, estimates games missed (stated timeline or historical median), optionally applies a minutes change, and returns his value, my max bid and my roto gain before and after.",
      inputSchema: { type: "object", properties: { player: { type: "string" },
        news_text: { type: "string", description: "The injury/news text, e.g. 'Left knee; MCL sprain, out 3 weeks'" },
        games_missed: { type: "number", description: "Override the estimate if known" },
        minutes_change_pct: { type: "number", description: "e.g. +15 for a bigger role, -20 for a smaller one" } }, required: ["player"] } },
  ];
  const WHATIF_NAMES = ["team_summary", "find_targets", "player_card", "bid_check", "compare_players", "punt_scenario",
    "standings", "record_sale", "undo_sale", "set_category_weights"];

  class Toolbox {
    constructor(board, extra, opts) { this.board = board; this.whatif = new WhatIf(board); this.news = new News(board, extra, opts); this.specs = TOOL_SPECS; }
    call(name, args) {
      args = args || {};
      if (WHATIF_NAMES.includes(name)) return this.whatif[name](args);
      if (name === "news_impact" || name === "injury_history") return this.news[name](args);
      throw new Error(`Unknown tool ${name}`);
    }
  }

  const api = { Board, WhatIf, News, Toolbox, TOOL_SPECS, normName, pyRound, ratio, closeMatches };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.GMCore = api;
})(typeof window !== "undefined" ? window : globalThis);
