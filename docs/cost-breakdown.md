# End-to-end cost breakdown

Data as of **2026-10-06**. Every figure is captured spend — token counts from
real API responses and charged-USD lines from run logs — priced through the
rate table below. Nothing here is a projection.

Scope: the full pipeline behind this site, from ingest to publish, for a
corpus of **355 analyzed videos** (360 index items; 5 skipped, 0 failed).

## Headline

| Scope | USD |
|---|---|
| Per-video processing to date (352 videos with cost records) | $49.32 |
| Relevance scoring to date (1,439 candidates scored) | $1.00 |
| LLM agent-run overhead (cron runs, last 30 days) | ~$0.15 |
| **Corpus total to date** | **≈ $50.5** |

Everything after processing — storage, site build, publish, and hosting — adds
**$0** (see Operations layer).

## Per-video processing

| Stage | Model | Volume | Total | Average |
|---|---|---|---|---|
| Summarize | gemini-3.5-flash-lite | 352 | $45.95 | $0.131 |
| Analyze | deepseek-flash / deepseek-v4-flash | 352 | $3.37 | $0.010 |
| **Total per video** | | | | **$0.140** |

Summarize cost is driven by transcript size. The pipeline escalates by video
length: up to 45 minutes is processed directly as video, 45 minutes to 2.5
hours switches to audio only, and longer recordings are split into windows.

| Duration | Videos | Average | Total |
|---|---|---|---|
| up to 45 min | 157 | $0.070 | $10.95 |
| 45 min – 2.5 h | 189 | $0.183 | $34.62 |
| 2.5 h + | 6 | $0.624 | $3.75 |

Distribution across the 352 costed videos: median **$0.11**, p90 $0.25, p95
$0.39, p99 $0.63, max $0.92. Videos longer than 45 minutes are 55% of the
corpus but **78% of the spend**.

## Relevance scoring

| Stage | Model | Volume | Total | Average |
|---|---|---|---|---|
| Relevance gate (yt-recs) | gemini-3.5-flash-lite | 1,439 | $1.00 | $0.0007 |

One small call per candidate video (metadata plus the profile prompt). Monthly
split: 1,364 scored in September ($0.94), 75 in October ($0.06).

## Monthly history

| Month | Videos analyzed | Spend | Average |
|---|---|---|---|
| 2026-08 | 14 | $3.76 | $0.268 |
| 2026-09 | 308 | $43.07 | $0.140 |
| 2026-10 (through 10-06) | 30 | $2.49 | $0.083 |

September was a backlog catch-up. At the current steady state of 5–10 videos
per day the analysis line runs **$12–42 per month**; a full-cap day costs
roughly $0.85–1.40.

## Operations layer (last 30 days)

| Component | Runs as | Cost |
|---|---|---|
| Daily digest drain | script-only cron job | $0 (its LLM steps are the per-video costs above) |
| Weekly recommendations digest | LLM cron run (deepseek-flash) | $0.026 per run |
| Gate tracker + gate insights jobs | LLM cron runs, 2 per day | ~$0.002 each |
| Tracker sync, consumption snapshot, channel suggestions, ingests, watchdogs | script-only cron jobs | $0 |
| Site build + publish | local build (~100 s CPU), 93 MB force-push | $0 |
| Hosting (GitHub Pages), domain, TLS | — | $0 marginal |
| Storage (git repositories + SQLite, local disk) | — | $0 |

## Rates used

From the pipeline's price table (`yt_insights/costs.py`, fetched 2026-09-10):

| Model | Input $/1M | Output $/1M | Cached $/1M |
|---|---|---|---|
| gemini-3.5-flash-lite | 0.30 | 2.50 | 0.03 |
| deepseek-flash, deepseek-v4-flash | 0.15 | 0.60 | 0.003 |
| deepseek-v4-pro | 0.66 | 1.98 | 0.022 |

DeepSeek prices are off-peak; peak is 2×. The daily drain runs at 05:00 UTC,
which is off-peak.

## Caveats

- Failed attempts are not fully priced: some error paths record no token
  usage, so retried work adds a small unpriced margin (observed during
  development: one error event consumed ~190K input tokens with no usage
  recorded).
- Prices are snapshots of provider pricing pages and drift over time; records
  are priced with the table current at recording time.
- Agent-run overhead is the charged USD per run and includes provider-side
  retries.

## Provenance

- Per video: `processed/<video_id>.json` → `costs.summarize` and
  `costs.analyze`, each `{model, input_tokens, output_tokens, cached_tokens,
  calls, duration_s, cost_usd}`.
- Per scored candidate: `recommendations/<channel>/<video_id>.json` →
  `meta.cost_usd` with usage.
- Per cron run: the run output carries a `$… USD · model · N API calls ·
  tokens` line.
- Corpus rollup: `index.json` items carry `cost_usd_total`; the site's
  `data/corpus.json` exposes computed counts.
