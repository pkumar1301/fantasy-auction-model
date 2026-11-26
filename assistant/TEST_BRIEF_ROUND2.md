# Test brief, round 2: GM assistant

**For:** Muse. **Repo:** `github.com/pkumar1301/fantasy-auction-model`, branch `project2-assistant`
(commit `fce1822` or later). **Builds on:** your round-1 report (2026-11-25).

## How the work is split this round

The assistant now also runs through a Claude subscription (Claude Code + an MCP server). Your machine has
internet access but no Claude access. The developer's machine has Claude access but news sites are blocked
there. So:

| Who | What |
|---|---|
| **Muse (you)** | Confirm the round-1 fixes, check live news sources, and **write a new question set the developer has never seen**, with expected answers worked out from the tools |
| Developer | Runs every Claude test (3 runs each), including your new set, and reports the results back |

**No Claude, Claude Code or API key is needed on your side.** Don't install Claude Code.

Your question set is the independent test of the assistant: the existing sets were used to tune the fixes.
Write it before looking at any results, and don't read `eval/questions.jsonl` or
`eval/conversations.jsonl` beyond their format.

## Ground rules

1. Don't change code under `assistant/` or `src/`. Report bugs with steps to reproduce.
2. Push only to a new branch `test-round2` on `pkumar1301/fantasy-auction-model`, holding just your question
   files and your report. Never push to `main`, `project2-assistant` or `gaspardcheng/fantasy-auction-model`.
3. Never commit `.env`, `logs/`, `auction-state.json` or results files.
4. Report what you saw. If a step can't run, say why and move on.

## 0. Setup

```bash
cd fantasy-auction-model && git fetch && git checkout project2-assistant && git pull
python3 -m venv .venv && . .venv/bin/activate
TMPDIR=$HOME/workspace/tmp-pip python3 -m pip install --no-cache-dir -r assistant/requirements.txt
```

## 1. Offline tests

```bash
for t in test_engine_parity test_tools test_agent_loop test_mcp_server test_eval_harness; do
  python3 -m assistant.tests.$t | tail -1; done
```
Expected: `PASS`, `73/73 passed`, `PASS`, `16/16 passed`, `6/6 passed`.

## 2. Round-1 bugs: confirm the fixes

| Bug | Command | Expected now |
|---|---|---|
| 1 | `python3 -m assistant.cli --tool news_impact --args '{"player":"Jayson Tatum","news_text":"out for season"}'` | games 82, live $39 → $0 (empty state) |
| 1 | same with `"done for the season"`, `"will miss the rest of the season"`, `"ACL tear, out for season"` | all 82 games; "rest of the season" not classed as Management |
| 1 | same with `"out indefinitely with a knee injury"` | basis says 90th percentile (about 33 games), not the median |
| 2 | `python3 -m assistant.cli --tool player_card --args '{"name":"LeBron Jamesson"}'` | JSON `{"error": ... Closest: ['LeBron James', ...]}`, exit 1, no traceback |

Re-run your full round-1 table of season-ending phrasings and report any that still give less than 82
games. Try 5 more phrasings real headlines use (e.g. "shut down for the year", "will undergo season-ending
surgery", "expected to miss 6-8 weeks", "out through the All-Star break") and report what each gives.

## 3. Live news (Bug 3 follow-up)

```bash
python3 -m assistant.cli --tool rss_news --args '{"player":"LeBron James","days":30}'
python3 -m assistant.cli --tool rss_news --args '{"player":"OG Anunoby","days":14}'
python3 -m assistant.cli --tool rss_news --args '{"player":"Kasparas Jakucionis","days":30}'
python3 -m assistant.cli --tool latest_injury_report --args '{"player":"OG Anunoby"}'
```

Check for each:
- [ ] `items_scanned_by_feed` lists every feed, including `Google News search`, with item counts
- [ ] LeBron: at least 3 items with `name_in: "headline"`, all within 30 days. Open 3 and confirm they're about
      him. Off-topic mentions (like the "sons in college" piece) are marked `summary only` and listed after.
- [ ] each item's `source` names the publisher (e.g. `SNY (via Google News search)`) and has a URL and a date
- [ ] the accented name (Jakučionis) is still found
- [ ] the `note` field reads correctly when results are thin

Then the full news chain at tool level (what the chat would do for "any news on Anunoby?"): take the newest
real Anunoby item you found and run `news_impact` with its text. Does the games-missed estimate make sense
against what the article says?

## 4. Write your own question set (the main job)

Phrase questions the way a real manager talks during a draft: short, informal, sometimes ambiguous. All
of them start from `assistant/eval/mid_auction_state.json` (14 sales; my team holds Maxey $62 and Murray
$48). Work out every expected answer by running the tools yourself:
`python3 -m assistant.cli --state assistant/eval/mid_auction_state.json --tool <tool> --args '<json>'`.

**A. 12 single questions:** `assistant/eval/questions_muse.jsonl`, same format as `questions.jsonl`
(`id`, `q`, `tool`, `ref`; add `"expect_refusal": true` for players not on the board). Include at least:
- 3 what-ifs combining position, budget and category needs
- 2 bid checks, one just above my max and one well below it
- 2 punt questions
- 2 news-to-value questions with made-up news text, written as a headline would be
- 1 player who isn't on the board
- 1 player already sold in the mid-auction state
- 1 vague question ("who's a steal right now?")

**B. 3 conversations:** `assistant/eval/conversations_muse.jsonl`, same format as `conversations.jsonl`
(see that file's `turns` checks: `tools_all`, `tools_any`, `tools_none`, `numbers`, `names_any`, `text_any`,
`text_none`, `state`). 3–5 turns each:
- one records two sales for my team and then asks about the budget (check `state.my_left` / `my_open`)
- one changes its mind ("actually, undo that"), and a later turn must reflect the undo
- one mixes a news question into a draft flow (made-up news text, so it doesn't depend on live feeds)

Keep the checks fair. Require what a correct answer must contain, not your preferred wording; use several
`text_any` alternatives. Prefer `numbers` and `state` checks over wording checks.

**C. Expected answers:** `assistant/eval/MUSE_EXPECTED.md`. For each question and turn, the tool call you ran
and the key values (top 3 names, prices, max bids, state). This is what the developer checks failures
against.

**D. Validate the files:** both must load, and every `ref` must run:
```bash
python3 - <<'PY'
import json
from assistant.agent import Toolbox
from assistant.engine import Board
st = json.load(open("assistant/eval/mid_auction_state.json"))
for l in open("assistant/eval/questions_muse.jsonl"):
    q = json.loads(l)
    try: Toolbox(Board(state=st)).call(q["tool"], q["ref"]); print(q["id"], "ok")
    except Exception as e: print(q["id"], "raises:", e, "(fine only if expect_refusal)")
for l in open("assistant/eval/conversations_muse.jsonl"):
    c = json.loads(l); print(c["id"], len(c["turns"]), "turns ok")
PY
```

## 5. Report and hand-off

Write `assistant/eval/TEST_REPORT_ROUND2.md`:
1. **Environment:** OS, Python, news-site access, date and time
2. **Steps 1–3:** pass/fail tables, including the extra phrasings and the live news checks
3. **Bugs:** numbered, with steps to reproduce, expected vs actual, and severity (wrong number > wrong player >
   wrong action > wording)
4. **Your question set:** what it covers, and anything you couldn't express in the check format

Commit `questions_muse.jsonl`, `conversations_muse.jsonl`, `MUSE_EXPECTED.md` and `TEST_REPORT_ROUND2.md`
to branch `test-round2` and push it. Reply with the commit hash. The developer will run your set 3 times
with Claude and send back the results, with every failure checked against your `MUSE_EXPECTED.md`.
