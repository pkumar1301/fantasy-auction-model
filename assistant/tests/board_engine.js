// Runs the auction board's own valuation code (docs/index.html) in Node, for the parity test.
// Usage: node board_engine.js <players.js> <state.json> <player names, comma-separated>
// Prints JSON: live/base/mymax/dme per requested player, inflation, and every team's roto points.
const fs = require("fs");
const [,, dataPath, statePath, namesArg] = process.argv;
const html = fs.readFileSync(__dirname + "/../../docs/index.html", "utf8");
const start = html.indexOf("// ---------------------------------------------------------------- valuation");
const end = html.indexOf("// ---------------------------------------------------------------- render");
if (start < 0 || end < 0) throw new Error("valuation block not found in docs/index.html");
const dataSrc = fs.readFileSync(dataPath, "utf8");
const window = {};
eval(dataSrc.replace("window.FANTASY_DATA", "window.FANTASY_DATA"));
const D = window.FANTASY_DATA, S = D.settings, L = D.league, CATS = S.cats;
const STATK = ["FGM","FGA","FTM","FTA","3PM","PTS","REB","AST","STL","BLK","TO"];
const P = D.players.map((p,i)=>({...p,id:i}));
const byName = new Map(P.map(p=>[p.name,p]));
const raw = JSON.parse(fs.readFileSync(statePath, "utf8"));
let st = {
  teams: Array.from({length:S.teams},(_,i)=>({name: i===0?"My team":"Team "+(i+1)})),
  me: 0, sales: [], weights: Object.fromEntries(CATS.map(c=>[c,1])), ...raw
};
eval(html.slice(start, end) + "\nglobalThis.__api = {rebuild, deltaMe, maxBid, getM: () => M};");
const api = globalThis.__api;
api.rebuild();
const M = api.getM();
const out = {inflation: M.inflation, money: M.money, slots: M.slots,
  teams: M.teams.map(t=>({left:t.left, open:t.open, maxBid:t.maxBid, pts:t.pts, rp:t.rp})), players: {}};
for (const n of (namesArg||"").split(",").filter(Boolean)) {
  const p = byName.get(n);
  out.players[n] = {live:p.live, base:p.base, vw:p.vw, mymax:p.mymax ?? null, dme:p.dme ?? null};
}
console.log(JSON.stringify(out));
