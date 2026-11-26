# Test brief, round 2: GM assistant on a Claude subscription

**For:** Muse (or any coding agent). **Repo:** `github.com/pkumar1301/fantasy-auction-model`,
branch `project2-assistant` (commit `22cb1f7` or later). **Builds on:** your round-1 report (2026-11-25).

Round 1 tested everything except the chat itself, because there was no API key. The assistant can now run
through a Claude subscription (Claude Code + an MCP server), so this round covers:

1. Confirming the round-1 bugs are fixed.
2. The live news sources, including the new per-player Google News search.
3. Parts C and D with Claude, 3 runs each, live items included.
4. **A new set of questions you write yourself**, which the developer has never seen. This is the only
   independent measure: the existing sets were used to tune the fixes.
5. A hands-on session through the MCP server.

## Ground rules (same as round 1)

1. Work on `project2-assistant`. Never push to `main`, and never push to `gaspardcheng/fantasy-auction-model`.
2. Don't change code under `assistant/` or `src/`. Report bugs with steps to reproduce. The only files you
   add are your own question sets (step 4) and your report.
3. Never commit tokens, `.env`, `logs/`, `auction-state.json` or results files.
4. Report what you saw. If a step can't run, say why and move on. Don't fill tables with guesses.

## 0. Setup

```bash
cd fantasy-auction-model && git checkout project2-assistant && git pull
python3 -m venv .venv && . .venv/bin/activate
TMPDIR=$HOME/workspace/tmp-pip python3 -m pip install --no-cache-dir -r assistant/requirements.txt
npm install -g @anthropic-ai/claude-code          # the `claude` CLI (Node 18+)
```

**Sign-in (subscription, no API key).** Prashant runs `claude setup-token` on his own machine. It prints a
long-lived token for his Claude subscription, which he gives you as a secret. Put it in your environment
and nowhere else:

```bash
export CLAUDE_CODE_OAUTH_TOKEN=...                 # from the secret store; never write it to a file in the repo
claude -p "Reply with OK" --tools ""               # must print OK
```

If you have an `ANTHROPIC_API_KEY` instead, you can use `--backend api` everywhere below. Say which one you
used. Note in the report whether your machine can reach news sites (ESPN, news.google.com, cms.nba.com).

**Usage budget.** One full pass (24 questions + 11 conversations) is about 1.6M tokens, mostly cache reads.
Claude Code's own estimate is about $2.40 per pass, and on a subscription this counts against usage limits
rather than being billed. Steps 3 to 5 come to roughly 8 passes. If you hit a usage limit, stop, note the
time, and resume later with `--only`. Don't switch models to get around it.

## 1. Offline tests (no Claude)

```bash
for t in test_engine_parity test_tools test_agent_loop test_mcp_server test_eval_harness; do
  python3 -m assistant.tests.$t | tail -1; done
```
Expected: `PASS`, `73/73 passed`, `PASS`, `16/16 passed`, `6/6 passed`.

## 2. Round-1 bugs: confirm the fixes

| Bug | Command | Expected now |
|---|---|---|
| 1 | `python3 -m assistant.cli --tool news_impact --args '{"player":"Jayson Tatum","news_text":"out for season"}'` | games 82, live $39→$0 (empty state) |
| 1 | same with `"done for the season"`, `"will miss the rest of the season"`, `"ACL tear, out for season"` | all 82 games; "rest of the season" not classed as Management |
| 1 | same with `"out indefinitely with a knee injury"` | basis says 90th percentile (about 33 games), not the median |
| 2 | `python3 -m assistant.cli --tool player_card --args '{"name":"LeBron Jamesson"}'` | JSON `{"error": ... Closest: ['LeBron James', ...]}`, exit 1, no traceback |
| 3 | `python3 -m assistant.cli --tool rss_news --args '{"player":"LeBron James","days":30}'` | see step 3 |

Re-run your round-1 table of season-ending phrasings and report any that still give less than 82 games.

## 3. Live news

```bash
python3 -m assistant.cli --tool rss_news --args '{"player":"LeBron James","days":30}'
python3 -m assistant.cli --tool rss_news --args '{"player":"OG Anunoby","days":14}'
python3 -m assistant.cli --tool latest_injury_report --args '{"player":"OG Anunoby"}'
```

Check for each:
- [ ] `items_scanned_by_feed` lists every feed, including `Google News search`, with item counts
- [ ] LeBron: at least 3 items with his name in the headline (`name_in: "headline"`), all within 30 days.
      Open 3 and confirm they're about him. The "sons in college basketball" type of item, if present, is
      marked `summary only` and listed after the headline matches.
- [ ] each item's `source` names the publisher (e.g. `SNY (via Google News search)`) and has a URL and a date
- [ ] the `note` field reads correctly when results are thin

## 4. Claude runs: Parts C and D, 3 runs each, live items included

```bash
python3 -m assistant.eval.run_eval --backend claude-code --model sonnet --runs 3
python3 -m assistant.eval.run_conversations --backend claude-code --model sonnet --runs 3
```

`run_conversations` runs your round-1 Part D scripts (`eval/conversations.jsonl`) with the values you
verified, including state checks after record / undo / punt. D3b (Anunoby news) is live.

| Measure | Bar per run |
|---|---|
| right_tool | ≥ 23/25 |
| dollars_ok, names_ok | 100%: every miss is either a bug or a scorer mistake. Say which. |
| top_pick | ≥ 7/8 |
| refusal_ok | 1/1 |
| conversations | ≥ 10/11 |

For **every** failing question or turn, copy the question, the tools called and the answer (from the CSV
or `conv_results_*.json`), and decide: agent wrong, scorer wrong, or data or network problem. Baseline from
the developer's single run: `eval/RESULTS.md`.

## 5. Your own question set (the independent test)

Write new questions without reading `eval/questions.jsonl` or `eval/conversations.jsonl` beyond their
format. Phrase them the way a real manager would talk during a draft: short, informal, sometimes ambiguous.

1. **12 single questions** in `assistant/eval/questions_muse.jsonl`, same format as `questions.jsonl`
   (`id`, `q`, `tool`, `ref`; `expect_refusal` / `live` where needed). Include at least:
   - 3 what-ifs combining position, budget and category needs
   - 2 bid checks, one just above and one well below the max
   - 2 punt questions
   - 2 news-to-value questions, using made-up news text so they don't depend on live feeds
   - 1 player that isn't on the board
   - 1 player already sold in the mid-auction state
   - 1 vague question ("who's a steal right now?")

   Work out `ref` by running the tool yourself (`--tool ... --state assistant/eval/mid_auction_state.json`)
   **before** running Claude, and write the expected answer down.
2. **3 conversations** in `assistant/eval/conversations_muse.jsonl`, same format as `conversations.jsonl`,
   3–5 turns each. At least one must record two sales and then ask about the budget; one must change its
   mind ("actually, undo that"); one must mix a news question into a draft flow. Get the expected numbers
   and state from tool calls first.
3. Run each 3 times:
   ```bash
   python3 -m assistant.eval.run_eval --backend claude-code --model sonnet --file assistant/eval/questions_muse.jsonl --runs 3
   python3 -m assistant.eval.run_conversations --backend claude-code --model sonnet --file assistant/eval/conversations_muse.jsonl --runs 3
   ```
4. Also judge each answer yourself (1–5) on whether a manager could act on it mid-draft: correct, short
   enough, and recommendation first. The scorer can't measure this.

## 6. Hands-on: MCP server through Claude Code

```bash
python3 -m assistant.mcp_server --print-rules > /tmp/gm_rules.txt
claude --system-prompt-file /tmp/gm_rules.txt --tools "" --allowedTools mcp__fantasy-gm
```
(`.mcp.json` in the repo registers the `fantasy-gm` server. Approve it if asked.)

1. Ask "what's my situation?" It should be the empty auction ($200, 10 spots) unless an
   `auction-state.json` already exists. Delete that file first to start fresh.
2. Record two sales for your team and one for another team, then quit Claude.
3. Start it again and ask for your budget. **The sales must still be there** (they're saved in
   `auction-state.json`).
4. Undo one sale, ask for targets at C, and check one target against
   `python3 -m assistant.cli --state auction-state.json --tool player_card --args '{"name":"..."}'`.
5. Note anything confusing about the experience: wording, speed, table formatting.

## Report

Write `assistant/eval/TEST_REPORT_ROUND2.md`. Push it, together with your two question files, to a new
branch `test-round2` on `pkumar1301/fantasy-auction-model`, or paste the report back if you can't push.

1. **Environment:** OS, Python, Node, Claude Code version, backend (subscription token or API key), model,
   news-site access, date and time
2. **Steps 1–3:** pass/fail tables
3. **Step 4:** the 3 run summaries for each script, tokens, time per question, and every failure triaged
4. **Step 5:** the 3 run summaries on your own set, your 1–5 ratings, and the 3 worst answers quoted
5. **Step 6:** what happened, including whether the state persisted
6. **Bugs:** numbered, with steps to reproduce, expected vs actual, and severity (wrong number > wrong player >
   wrong action such as an unrequested sale > wording)
7. **Top 3 fixes**, and whether you'd trust the assistant during a real draft, and why
