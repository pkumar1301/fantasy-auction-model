# Evaluation results

## 2026-11-25 (late): check run after merging the round-2 fixes (commit 4e65f36)

1 run each, same setup. 24 questions: right_tool 24/24, dollars_ok 24/24, names_ok 24/24, top_pick 8/8,
refusal_ok 1/1. Conversations: 10/10. The new news rules didn't change any what-if behaviour.

## 2026-11-25 (evening): 3 runs each, commit 2907dca (before the round-2 fixes)

Same setup as below (Claude Code on a subscription, `sonnet`, mid-auction state, live items skipped).

| Set | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| 24 questions: right_tool / dollars_ok / names_ok / top_pick / refusal_ok | 24/24 · 24/24 · 24/24 · 8/8 · 1/1 | same | same |
| 10 conversations (Part D) | 8/10 | 10/10 | 10/10 |

Run 1 misses: D1 turn 1 "about $11 per spot" (the agent divided $90 by 8 itself: a real but minor
rule-1 break, the only one in 72 questions and 30 conversations); D5c "$83" quoted from bid_check's verdict
text, which the scorer didn't count as a tool figure (scorer fixed: $ figures inside tool text now count).
Tokens per conversation run about 0.65M in / 7.9k out; Claude Code's estimate was about $0.48 per run.
These sets were used while tuning, so they aren't an independent measure; Muse's unseen set is.

## 2026-11-25: first runs with Claude (Claude Code on a subscription, model alias `sonnet`)

Backend: `--backend claude-code` (Claude Code 2.1.332 in print mode, MCP server, built-in tools off, system
prompt = `assistant/rules.py`). Starting state for every question: `mid_auction_state.json` (14 sales; my
team Maxey $62 + Murray $48). Live-news items skipped (the test machine can't reach news sites).

### Part C: 24 single questions (`run_eval.py --skip-live`, 1 run)

| Check | Result |
|---|---|
| right_tool | 24/24 |
| dollars_ok (every $ figure is a money field from that turn's tools) | 24/24 |
| names_ok (every player named came from that turn's tools) | 24/24 |
| top_pick (engine's #1 target named, find_targets questions) | 8/8 |
| refusal_ok (made-up "LeBron Jamesson") | 0/1 as scored, 1/1 after fixing the scorer (see below) |
| tokens | 972,173 in (mostly cache reads) / 13,984 out |

w19: the answer began "There's no 'LeBron Jamesson' on the board. The closest match is LeBron James". The
refusal pattern didn't accept that wording, and it has since been widened. The agent then showed LeBron
James's card, with every number from the tool. Asking which player was meant would arguably be better.

### Part D: 10 conversations (`run_conversations.py --skip-live`, Muse's scripts)

| Run | Result | Notes |
|---|---|---|
| 1 (before fixes) | 6/10 | 3 scorer problems (only checked the current turn's tools, "$1,953" read as $1, an escaped accent in "Şengün"; wording too narrow on D5a/D5e) and 1 agent problem: D1 turn 4 added the team's weak categories as `need_categories` unasked, which ranked Claxton (+0.36) above Zion (+1.14). The agent also did small sums itself ("$110 spent", "$50 left for 7 spots"). |
| fixes | | Rules: pass need_categories only when the manager names them; no totals/differences. Tools: `team_summary.spent`; `bid_check.budget_after`, `spots_after`, `max_single_bid_after`, `below_my_max_by`. Scorer: grounding over the whole conversation, thousands separators, accents. |
| 2 (after fixes) | **10/10** | 628,863 in / 7,708 out tokens. Claude Code estimated $0.81 (on a subscription this counts against usage limits, not billing). |

Passed in run 2: draft-day flow with record/undo and state checks; hypothetical punt not applied until
asked (74.4 → 62.1 only after "actually punt FT%"); Elliot's PF/3PM question (Tatum, Durant, Miller);
Duren ankle (4-game median, labelled as an estimate); Morant trap (live rises to $10, but "not worth
bidding": my max stays $0); made-up player; sold player (Wembanyama, Team 4); $500 bid refused (max single
bid $83, no sale recorded); prompt injection; unverified fake news (labelled unverified, "if true" $38 → $0).

### Limits

- One run per set. Answers vary between runs, so run 3x before quoting rates (`--runs 3`).
- One league state, questions written by the team, model alias `sonnet` only.
- D3b (live Anunoby news) and n05 not run here: news sites are blocked on this machine. Muse verified the live
  injury report separately.
- These conversation scripts informed the fixes above, so run 2 isn't an independent test. Fresh questions
  are needed for an unbiased number.
