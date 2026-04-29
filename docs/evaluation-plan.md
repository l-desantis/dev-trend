# Evaluation Plan — DevTrend Phase 1

> **Purpose:** A manual-review checklist to validate that the pipeline produces trustworthy, actionable briefs. Run weekly for the first month after launch, then monthly thereafter.
>
> Use the [replay harness](#using-the-replay-harness) to generate synthetic history before reviewing live results.

---

## 1. Evidence Fidelity

**Prompt:** Does each evidence item in a brief link to a real, relevant source?

**Checks:**

- [ ] Open the latest brief for a niche via `/niche <slug>`.
- [ ] For each evidence item listed, copy the URL and confirm it resolves (HTTP 200, not 404 or redirected to a generic page).
- [ ] Confirm the excerpt shown in the brief appears verbatim (or close paraphrase) in the linked source.
- [ ] Verify the source type matches the URL domain: `github` → `github.com`, `hn` → `news.ycombinator.com`, `reddit` → `reddit.com`.
- [ ] If any evidence item is missing a URL or the URL is clearly wrong, log an issue.

**Pass criteria:** ≥ 90 % of evidence items link to a real, topically relevant source.

---

## 2. Score Interpretability

**Prompt:** Do the scores rank niches in an order that matches manual intuition?

**Checks:**

- [ ] Run `/niches` and record the top 5 niches by score.
- [ ] For each niche, read the one-line summary and judge whether the score feels proportionate (a niche with lots of recent GitHub/HN/Reddit activity should rank higher than a quiet one).
- [ ] Check score breakdown (Growth / Demand / Novelty) via `/niche <slug>`. Confirm each sub-score is plausible given what you know about the niche's activity level.
- [ ] Verify that a niche with no signal data in the last 7 days scores lower than one with daily mentions.
- [ ] Run `/niche <slug>` on a niche you know to be stagnant. Confirm `forecast_label` is `Stable` or `Declining`, not `Rising`.

**Pass criteria:** Top-5 ranking matches expert intuition for ≥ 4 out of 5 niches.

---

## 3. Signal Freshness

**Prompt:** Is the data powering the briefs recent enough to be actionable?

**Checks:**

- [ ] Run `/sources` and confirm all four connectors (GitHub, HN, Reddit, App Store) show a `last_run_at` timestamp within the last 12 hours.
- [ ] Check the DB directly: `SELECT MAX(ingested_at) FROM source_items GROUP BY source_type;` — each source type should have rows from today.
- [ ] For at least one niche, verify that evidence items are from the last 7 days (timestamps visible in `/niche <slug>` output).
- [ ] If any source shows `never run` or a timestamp older than 24 hours, investigate connector logs.

**Pass criteria:** All four sources have ingested data within 12 hours.

---

## 4. Slope Usefulness

**Prompt:** Does the Growth slope label (`Rising` / `Stable` / `Declining`) reflect recent momentum accurately?

**Checks:**

- [ ] Identify a niche you expect to be trending (e.g. a topic with recent HN front-page coverage).
- [ ] Confirm `/niche <slug>` shows `Rising` label and a positive growth breakdown.
- [ ] Identify a niche that has been quiet for 2+ weeks. Confirm it shows `Stable` or `Declining`.
- [ ] Using the replay harness with `--profile rising`, confirm that a niche whose signal count doubles over 60 days scores `Rising` at the end of the window.
- [ ] Using the replay harness with `--profile flat`, confirm scores remain in a narrow band (± 5 points) across the 60-day window.

**Pass criteria:** Slope label matches expected direction for ≥ 80 % of sampled niches.

---

## 5. Notification Quality

**Prompt:** Are Telegram notifications readable, accurate, and not spammy?

**Checks:**

- [ ] Trigger a daily digest manually (restart bot or wait for cron). Verify the digest message arrives and contains the top-3 niches with scores and arrows.
- [ ] Confirm no MarkdownV2 parse errors appear in bot logs (Telegram rejects malformed messages silently; check for `TelegramError` in logs).
- [ ] Send `/briefing` — confirm the response is ≤ 4096 characters and is formatted readably (bold headers, score arrows).
- [ ] Send `/niche <slug>` for a niche with a long brief — confirm the message is truncated gracefully (ends with the truncation footer, not cut mid-sentence).
- [ ] Verify that non-allowlisted chat IDs receive the rejection message ("This bot is private.") and no further output.

**Pass criteria:** All notifications arrive formatted correctly with no parse errors.

---

## 6. Spike Alert Accuracy

**Prompt:** Does the spike alert fire only when score growth is genuinely large?

**Checks:**

- [ ] Check `SPIKE_ALERT_THRESHOLD` in `.env` (default: 15 points). Confirm the value is intentional.
- [ ] Manually seed a `NicheScoreHistory` row with `score_total = prior + 16` for a niche, then restart the scoring job. Verify a spike alert is sent to the allowed chat.
- [ ] Confirm no spike alert fires when the delta is 14 (one below threshold). Edit the seed row and rerun.
- [ ] Over one week of live data, record how many spike alerts fire. If > 3/day on average, consider raising the threshold; if 0/week, consider lowering it.

**Pass criteria:** Spike alerts fire within 1 point of the configured threshold, with no false positives in a quiet period.

---

## Cadence

| Period | Review frequency |
|---|---|
| First month post-launch | Weekly — all six criteria |
| Months 2–3 | Every two weeks — focus on Score Interpretability and Spike Alert Accuracy |
| Steady state (month 4+) | Monthly — spot-check Evidence Fidelity and Signal Freshness |

---

## Using the Replay Harness

The replay harness (`scripts/run_replay.py`) seeds synthetic `NicheSignal` rows and re-runs the daily-scoring loop day-by-day, letting you evaluate scoring behaviour without waiting for real ingestion.

**Safety:** the script aborts unless `DATABASE_URL` contains `replay` or `:memory:`. Use a dedicated replay DB:

```bash
DATABASE_URL=sqlite+aiosqlite:///./devtrend-replay.db \
  uv run python scripts/run_replay.py --days 60 --profile rising --yes
```

**Profiles:**

| Profile | Behaviour | What to check |
|---|---|---|
| `flat` | Constant mention counts | Scores should stay in a narrow band (± 5 pts); no `Rising` labels |
| `rising` | Linearly increasing counts | End-score should be ≥ 20 pts above start-score; final label should be `Rising` |
| `spiky` | Periodic spikes every 7 days | Spike-alert threshold fires on spike days; slope oscillates around `Stable` |

**Targeting specific niches:**

```bash
uv run python scripts/run_replay.py --days 30 --profile spiky \
  --niches ai-habit-trackers,no-code-saas --yes
```

**Expected summary table output (rising profile, 60 days):**

```
========================================================================
Niche                           Min    Max    End  Slope
------------------------------------------------------------------------
  ai-habit-trackers           28.4   91.2   89.7  ↑ Rising
  no-code-saas                26.1   88.5   87.3  ↑ Rising
========================================================================
```

If the `End` score for the rising profile is below 70, or the slope label is not `↑ Rising`, investigate the Growth scoring path (`app/forecasting/scoring.py`).

---

## v4 Evaluation Criteria

These checks apply specifically to the v4 opportunity-discovery pipeline introduced in Plan B.

### 7. Extraction Precision

**Prompt:** Are extracted PainPoints genuine unmet-need signals?

- Randomly sample 50 `PainPoint` rows from `pain_points` table.
- For each, read `problem_text` and judge: is it a real, specific user complaint that implies a missing product?
- Log any that are noise (off-topic, too vague, or a positive statement).

**Target:** ≥ 80 % are genuine unmet-need signals.

---

### 8. Cluster Coherence

**Prompt:** Do the pain points within an `OpportunityCandidate` belong together?

- For each candidate with ≥ 5 attached `PainPoint` rows, read all 5.
- Rate "do these 5 problems belong to the same candidate?" on a 1–5 scale (1 = wildly mixed, 5 = tightly coherent).
- Target average ≥ 4.0 across 20 sampled candidates.

---

### 9. Specificity Calibration

**Prompt:** Is the LLM's specificity score calibrated?

- Rate 20 randomly-selected `OpportunityCandidate` rows yourself on a 1–5 specificity scale.
- Compare to the stored `specificity` field.
- Compute Spearman rank correlation.

**Target:** Spearman ρ ≥ 0.6.

---

### 10. Lifecycle Stability

**Prompt:** Do lifecycle states stay stable, or do candidates bounce day-to-day?

- After 7+ days of scoring data, count candidates that changed `lifecycle_state` more than once in 7 days.
- Express as a percentage of all scored candidates.

**Target:** < 5 % of candidates bounce states in a 7-day window. If above, consider adding hysteresis (require state to hold for 2 consecutive scoring runs before transitioning).
