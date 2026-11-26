// Node side of parity_test.py: run each case's tool calls through gm_core.js and print the results as JSON.
const fs = require("fs");
const G = require("./gm_core.js");
const [, , dataPath, casesPath] = process.argv;
const data = JSON.parse(fs.readFileSync(dataPath, "utf8"));
const cases = JSON.parse(fs.readFileSync(casesPath, "utf8"));
const out = cases.map(c => {
  const tb = new G.Toolbox(new G.Board(data.board, c.state), data.extra, { web: false });
  return c.steps.map(([name, args]) => {
    try { return JSON.parse(JSON.stringify(tb.call(name, args))); }
    catch (e) { return { __error__: e.message }; }
  });
});
process.stdout.write(JSON.stringify(out));
