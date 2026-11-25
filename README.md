# Fantasy Basketball Auction Model

> Fork of [gaspardcheng/fantasy-auction-model](https://github.com/gaspardcheng/fantasy-auction-model). Team project, AI Edge Project 1 (15.071), with Gaspard Cheng, Caleb Drake, Elliot Forman and Prashant Kumar.

Predicts 2026-27 NBA per-game stats and games played, turns them into auction dollar values for a 14-team,
9-category rotisserie league, and measures the gain in league standings by replaying a past season. Trained on
30 seasons of box scores (1996-97 to 2025-26) plus the free text of the NBA's official injury reports.
Full method, every table and all caveats: [METHODS.md](METHODS.md).

## Reproduce (about 70 minutes, CPU only)

```bash
python3 -m pip install -r requirements.txt
bash run_all.sh          # full pipeline: model selection → backtest → figures → boards
open docs/index.html     # live auction board
```

All data is in `data/`. `Project1_data_package/scripts/` and `src/fetch_injury_reports.py` rebuild it from
public sources (optional, about 1 hour each).

## Data

* `Data 1 extended.csv`: per-game stats for every player, 1996-97 to 2025-26 (14,569 player-seasons). It
  matches the original `Data 1.csv` (2021-22 to 2025-26) on all 2,867 shared rows.
* `player_season_extra.csv`: usage, efficiency, pace, height, teams and team games played.
* `injury_reports.csv`, `injury_reports_pre2021.csv`: official game-day injury reports, Dec 2018 to the 2026 Finals.
* `Data 2.xlsx`: third-party 2026-27 projections and positions (used only for the 2026-27 board).
  `Data 3.xlsx`: games per team per week (head-to-head boards only).

## Method

* **Task.** For each season *t*, use only what is known on draft day (seasons *t-1* to *t-3*) to predict
  season *t*. Players are scored as the sum of 9 category z-scores per game. Season value adds games played.
* **Validation.** Rolling origin: validate on each season from 2015-16 to 2024-25, training on all earlier
  seasons. 22 candidate models are compared on these folds only. **2025-26** is tested once, at the end.
* **Selected model.** Ridge regression for per-game stats, gradient boosting for games played. It beat both
  benchmarks (*Naive*: last season repeats; *Marcel*: weighted 3-year average with age curve) in all 10 folds.
* **Injury text.** A sentence encoder plus keyword rules sort report reasons into body region and type.
  Knee injuries and surgery cost 6 to 9 games the next season. The text adds little to the forecast once
  30 seasons of history are used.

## Results (hold-out 2025-26, 353 players)

| Model | Value/game MAE | Games MAE | Season-value Spearman |
|---|---|---|---|
| Naive (last season) | 1.874 | 18.67 | 0.664 |
| Marcel (weighted avg) | 1.843 | 17.56 | 0.651 |
| **Ridge + boosting (selected)** | **1.712** | **16.52** | **0.696** |

Against Marcel, value/game error falls 7.1% [95% CI 2.0%, 12.2%] (bootstrap by player).

**Auction backtest.** 1,000 simulated 14-team $200 auctions for 2025-26 in each of three rival rooms, scored on
actual stats:

| Rivals bid with | Our roto points (of 126) | vs a GM bidding like the room | Win league |
|---|---|---|---|
| Marcel | 76.2 | +9.3 | 17.9% |
| Naive | 77.7 | +11.0 | 31.8% |
| Half and half | 74.7 | +7.0 to +8.9 | 21.8% |

## Limitations

The auction gain rests on one replayed season. Rookies fall back to Data 2. Rivals are simple and nominations
are not strategic. The 2026-27 board blends our model 50/50 with Data 2, a judgment call.

## Repo map

`src/` holds the scoring, features, models and injury text. `docs/index.html` is the roto auction board (other
formats: `roto12.html`, `h2h.html`, `h2h14.html`). `results/` holds every output table and figure.
