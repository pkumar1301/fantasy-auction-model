# GM assistant (Project 2)

A Claude agent on top of the Project 1 auction model. The manager asks in plain English; the agent answers by
calling tools that run the board's own valuation code, so every price, max bid and roto-point figure matches
the auction board (`docs/index.html`).

Two modules so far:

* **What-if chat** (Elliot's use case 1): "I have $40 left, I'm weak in threes and only have a PF spot open.
  Who should I target?", "What if I punt FT%?", "Anunoby is at $30, keep bidding?"
* **News agent** (Elliot's use case 2): "I'm thinking of bidding on Haliburton. Any news?" It checks the
  official injury reports and news feeds (plus a search API, if a key is set), then re-values the player:
  injury text → injury type → games missed (stated timeline, or the historical median for that injury from
  Project 1) → new value, max bid and roto gain, before vs after.

## Setup

```bash
python3 -m pip install -r requirements.txt -r assistant/requirements.txt
cp assistant/.env.example .env        # then add your ANTHROPIC_API_KEY (and optionally a search key)
```

## Use

```bash
python3 -m assistant.cli                                    # chat in the terminal
python3 -m assistant.cli --state auction-state.json         # start from the board's Export file
python3 -m assistant.cli --ask "Who's the best center for me under $20?"
python3 -m assistant.cli --tool find_targets --args '{"position":"PF","need_categories":["3PM"],"budget_left":40,"open_spots":1}'
```

`--tool` calls one tool directly with no LLM and no key, which is handy for checking numbers. In chat,
`/save file.json` writes the auction state, which the board can import.

## How it works

```
manager question
   └─ agent.py: Claude tool-use loop (system prompt: every number must come from a tool)
        ├─ tools/whatif.py   team_summary, find_targets, player_card, bid_check, compare_players,
        │                    punt_scenario, standings, record_sale, undo_sale, set_category_weights
        ├─ tools/news.py     injury_history, latest_injury_report, rss_news, search_news, news_impact
        └─ engine.py         Python port of the board's valuation (live price, max bid, roto points)
```

* `engine.py` mirrors the `valuation` block of `docs/index.html` line for line.
  `tests/test_engine_parity.py` runs the board's JavaScript in Node and checks that live prices, start
  prices, max bids, roto gains and all 14 teams' points match (180/180 values, across 3 scenarios).
* Hypotheticals ("if I had $60 and 3 spots") run on a copy of the board and never change the real state.
* `news_impact` uses the same injury keyword rules as `src/injury_types.py` (read from that file, so they
  stay in sync) and the games-missed table `results/injury_absence_by_type.csv`. A minutes change
  (trade, new role) scales the player's per-game line and recomputes his z-scores the way
  `src/build_values.py` does.
* Each turn is logged to `logs/assistant_*.jsonl`: question, tool calls and outputs, answer, tokens, time.

## Tests and evaluation

```bash
python3 -m assistant.tests.test_engine_parity   # Python engine = board JavaScript (needs node)
python3 -m assistant.tests.test_agent_loop      # agent loop with a fake Claude client (offline)
python3 -m assistant.eval.run_eval --skip-live  # 24 questions scored against the engine (needs API key)
```

`eval/questions.jsonl` has 20 what-if and 5 news questions, all starting from the same mid-auction state
(14 sales; my team holds Maxey $62 and Murray $48). The scorer checks the right tool was called, the engine's
top pick is named, and every $ figure and player name in the answer appears in that turn's tool results. It
also includes one made-up player to check that the agent says he isn't on the board.

## Limits (state these in the write-up)

* Tested on the 2026-27 roto board (14 or 12 teams). The head-to-head boards use a different engine and
  aren't supported yet.
* The evaluation runs one league state with one run per question, and the questions were written by the
  team, not by real managers.
* Games-missed estimates are historical medians when no timeline is reported. The 90th percentile is shown
  because injuries have long tails.
* Free feeds cover headlines, not every beat report. The search API fills gaps but needs a key, and results
  depend on the provider.
* Max bid is found by the board's 9-step search, so it can land $1 below the true break-even price.
  Kept as-is so the chat matches the board.
* Live sources (injury-report PDF, feeds, search APIs) are written to their public formats but haven't yet
  been run against the live sites from this repo. Run `n05` in the eval to confirm.
