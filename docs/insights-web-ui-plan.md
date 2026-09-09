# YT Insights Explorer — implementation plan

Status: planning only  
Target repository: `/home/rmax-10/src/rmax-ai/yt-insights-explorer`  
Source repository (read-only input): `/home/rmax-10/src/rmax-ai/yt-insights`

## 1. Goals and scope

### Goals

Build a deterministic, backend-free website that lets Max browse a private YouTube-derived knowledge corpus by time, concept, idea, claim, and source video. The generated output must be ordinary HTML, CSS, JavaScript, and JSON that can be opened locally or copied to any static host.

The product must:

- turn the source repository's analyzed artifacts into a validated, path-free normalized corpus;
- provide overview, trend, concept, idea, claim, video, and search views;
- preserve provenance: every extracted item links to its local video detail page, and every video page links to the actual YouTube `source_uri` and displays channel and publication date;
- render all structured insight sections on each video page;
- use relative internal URLs and a configurable deployment base path;
- work within a 2-CPU/3.7 GB machine, complete a 73-video build in seconds or low minutes, and remain comfortably below 25 MB (GH Pages limit is 1 GB; budget is a self-imposed leanness gate);
- surface that the content is private and unreviewed until an explicit publication decision changes build configuration.

### In scope

- Reading `index.json`, analyzed videos' `summary.md`, and `insights.json` at build time.
- Schema validation with actionable file-and-field errors.
- Pre-rendered pages and precomputed aggregate JSON.
- Small inline SVG charts and a deterministic concept connection graph.
- Vanilla-JavaScript progressive enhancement for filters, graph highlighting, and search.
- A command-line builder, automated tests, fixture corpus, and generated-site integrity checks.
- A configurable site title, base path, and publication mode.

### Out of scope

- A backend, database, API, authentication system, server-side search, Node, React, or a client framework.
- Modifying or committing in the source repository.
- Re-running ingestion, transcription, summarization, or insight extraction.
- Inventing timestamps, external citations, verification results, or inferred relationships.
- Embedding transcripts. Legacy transcript paths may be present in the index, but transcripts are not part of this product's content contract.
- Editing notes in the browser, user accounts, annotations, collaboration, or analytics tracking.
- Publishing private notes automatically. Generation and publication are separate actions.

## 2. Verified source reality and corrections

Inspection on 2026-09-09 covered the named 2025 artifact, a 2026-01 artifact, and both 2021 artifacts. It also scanned all 73 `insights.json` files for field shape and URLs.

- `index.json` is actually an object of shape `{ "items": [...] }`, not a bare list. The builder must require the `items` key. This is the only correction to the supplied source description.
- The index has 83 items: 73 `analyzed`, 5 `skipped`, and 5 `failed`; summed non-null `cost_usd_total` is `12.621487` USD.
- All 73 analyzed `insights.json` files have the same ten top-level keys and the stated nested fields.
- Observed controlled values additionally include `evidence_strength = weak|moderate|strong`, `novelty = low|medium|high`, `deep_dives.priority = low|medium|high`, `key_claims.claim_type = causal|comparative|factual|opinion|prediction`, and Boolean `key_claims.verification_needed`.
- Summary frontmatter also contains `status`; retain it in normalization.
- No `http://` or `https://` URLs occur in any `insights.json` or summary body. The only current outbound source link is summary frontmatter `source_uri`.
- There are no usable evidence timecodes. Do not render a fake timestamp or timestamp link.

## 3. Architecture

### Decision: live source path, not a checked-in corpus snapshot

The source repository path is passed to the CLI with `--source`. Do not check a full exported snapshot into this web repository. This avoids duplicating private notes, prevents a stale second source of truth, and lets a build target any checkout or revision. Git already versions the canonical corpus. Only 3–5 deliberately selected real artifact copies belong in this repository as test fixtures.

The exact primary command is:

```sh
uv run build_site --source /path/to/yt-insights --out site
```

`--source` is required. `--out` defaults to `site`, so this is also valid:

```sh
uv run build_site --source /path/to/yt-insights
```

Optional CLI flags:

```text
--base-path PATH       deployment prefix, default "./"; accept "/yt-insights/"
--publication MODE     private|public, default private
--acknowledge-private-unreviewed
                       required with public mode while included records remain private/unreviewed
--site-title TEXT      default "YT Insights Explorer"
--generated-at ISO     optional display metadata only; omitted by default
```

Do not read the current clock, hostname, source absolute path, Git branch, or random values into generated files. If `--generated-at` is absent, show no build timestamp. `--publication public` permits generation of a publishable copy but does not deploy it. While any included record is private or unreviewed, public mode must fail unless `--acknowledge-private-unreviewed` is also present; that flag is the explicit publication decision. Public mode changes the site-wide warning/noindex treatment, but video pages still display each record's true visibility and review metadata.

### Build flow

```text
read-only source checkout
  index.json {items:[...]}
  artifacts/.../summary.md + insights.json
          |
          v
load + validate + normalize in memory
  resolve artifact paths safely beneath source/artifacts
  parse frontmatter and Markdown body
  drop all host paths
  assign stable item IDs and slugs
          |
          v
derive aggregates
  trends, concepts/adjacency, ideas, claims, search index
          |
          v
render to a temporary sibling directory
  HTML pages + CSS/JS + normalized JSON
          |
          v
validate generated tree, then atomically replace --out
```

The builder may use an index artifact's absolute path only to locate a source file after verifying that its resolved path is under `<source>/artifacts`. Prefer locating the analyzed directory by matching the final artifact directory and filenames; never serialize the input path. A missing, escaping, or mismatched path is a build error.

Use Python 3.12+, `jinja2` for autoescaped templates/includes, `PyYAML` with `yaml.safe_load` for existing OKF frontmatter, and `markdown-it-py` configured with raw HTML disabled for summary-body rendering. These small dependencies buy correct escaping and robust YAML/Markdown handling; handwritten HTML assembly is not worth the injection and maintenance risk. Runtime website code is dependency-free vanilla JS.

### Determinism contract

Every JSON file is UTF-8 with LF endings, `ensure_ascii=False`, `sort_keys=True`, compact separators, and one final newline. Templates receive already ordered sequences. Never depend on filesystem iteration, set iteration, locale collation, hash order, or current time.

Canonical sort rules:

- videos: `(source_published desc, video_id asc)` for display; `(video_id asc)` for emitted filenames and build traversal;
- months: ASCII `YYYY-MM` ascending;
- tags/concepts: `(-video_count, canonical_name.casefold(), canonical_name)` for ranked display; `(slug, canonical_name)` for emitted pages;
- insight types and enum filters: fixed source-domain orders declared in code, never alphabetical by accident;
- all extracted item lists: `(source_published desc, video_id asc, source_index asc)` unless a page declares a fixed priority/fits grouping first;
- connections: `(source_concept_id, target_concept_id, relationship.casefold(), video_id, source_index)`;
- search records: `(kind_order, title.casefold(), video_id, item_id)`;
- dict output: JSON key order is lexical due to `sort_keys=True`.

Input list order is semantically retained with `source_index` and used on video pages. Stable IDs are `<video_id>:<section>:<zero-based source_index>`, for example `r8RGYw1n-5k:core_insights:0`. Concept IDs are `c-<slug>-<8 hex chars>`, where the suffix is the first eight lowercase hex characters of SHA-256 over the UTF-8 canonical name; this avoids slug collisions deterministically.

## 4. Normalized data model

The normalized corpus is emitted for inspectability under `data/`, but HTML pages are rendered from the same in-memory objects and do not fetch JSON. Null means unavailable; missing and empty source strings normalize to null only where the schema allows it. Dates are preserved as UTC ISO 8601 strings and also derive a `YYYY-MM` month.

### `data/corpus.json`

```json
{
  "schema_version": 1,
  "site": {
    "title": "YT Insights Explorer",
    "publication_mode": "private",
    "base_path": "./"
  },
  "counts": {
    "index_items": 83,
    "analyzed_videos": 73,
    "skipped_videos": 5,
    "failed_videos": 5,
    "concepts": 0,
    "core_insights": 0,
    "article_ideas": 0,
    "project_ideas": 0,
    "deep_dives": 0,
    "open_questions": 0,
    "claims": 0
  },
  "total_cost_usd": 12.621487,
  "video_ids": ["..."],
  "months": ["2021-05", "..."]
}
```

`video_ids` is sorted by `(source_published desc, video_id asc)`. Counts are computed, never copied from assumptions. Cost sums non-null index values without rounding; presentation may format to two decimals.

### `data/videos/<video_id>.json`

Each document has exactly these fields:

```json
{
  "schema_version": 1,
  "video_id": "r8RGYw1n-5k",
  "slug": "what-everyone-missed-about-gemini-3-r8rgyw1n-5k",
  "url": "videos/what-everyone-missed-about-gemini-3-r8rgyw1n-5k/index.html",
  "title": "...",
  "channel": "Maxi",
  "status": "analyzed",
  "ingested_at": "2026-09-08T...+00:00",
  "cost_usd_total": 0.123,
  "source": {
    "type": "youtube",
    "uri": "https://www.youtube.com/watch?v=r8RGYw1n-5k",
    "title": "...",
    "author": "Maxi",
    "published_at": "2025-11-20T21:01:05Z",
    "published_date": "2025-11-20",
    "published_month": "2025-11"
  },
  "document": {
    "type": "Digest",
    "description": "...",
    "urn": "urn:rmax-ai:digest:r8RGYw1n-5k",
    "status": "complete",
    "confidence": "high",
    "visibility": "private",
    "captured_at": "2026-09-08T...+00:00",
    "generated_by": "gemini-3.5-flash-lite",
    "review_status": "unreviewed"
  },
  "summary": {
    "markdown": "## Overview\n...",
    "html": "<h2>Overview</h2>..."
  },
  "tags": [
    {"name": "ai agents", "concept_id": "c-ai-agents-...", "url": "concepts/ai-agents-.../index.html"}
  ],
  "core_insights": [
    {
      "id": "r8RGYw1n-5k:core_insights:0",
      "source_index": 0,
      "insight": "...",
      "type": "architecture",
      "why_it_matters": "...",
      "generalization": "...",
      "evidence_quotes": [{"text": "...", "timestamp_seconds": null, "source_url": null}],
      "evidence_strength": "strong",
      "novelty": "high"
    }
  ],
  "deep_dives": [
    {
      "id": "...:deep_dives:0", "source_index": 0, "topic": "...",
      "research_question": "...", "why": "...", "trigger_insight": "...",
      "evidence_quotes": [{"text": "...", "timestamp_seconds": null, "source_url": null}],
      "priority": "high"
    }
  ],
  "article_ideas": [
    {"id": "...:article_ideas:0", "source_index": 0, "title": "...", "thesis": "...", "angle": "...", "based_on": "...", "audience": "..."}
  ],
  "project_ideas": [
    {"id": "...:project_ideas:0", "source_index": 0, "name": "...", "hypothesis": "...", "poc": "...", "measurement": "...", "based_on": "...", "fits": "gatehouse"}
  ],
  "architectural_implications": [
    {"id": "...:architectural_implications:0", "source_index": 0, "observation": "...", "before": "...", "after": "...", "consequence": "..."}
  ],
  "tradeoffs_and_failure_modes": [
    {"id": "...:tradeoffs_and_failure_modes:0", "source_index": 0, "topic": "...", "benefit": "...", "cost_or_risk": "...", "evidence_quote": {"text": "...", "timestamp_seconds": null, "source_url": null}}
  ],
  "open_questions": [
    {"id": "...:open_questions:0", "source_index": 0, "question": "...", "why_unresolved": "...", "research_direction": "..."}
  ],
  "key_claims": [
    {"id": "...:key_claims:0", "source_index": 0, "claim": "...", "claim_type": "factual", "evidence": "...", "verification_needed": true, "verification_question": "...", "verification_status": "needed"}
  ],
  "connections": [
    {"id": "...:connections:0", "source_index": 0, "concept": "...", "concept_id": "c-...", "connects_to": "...", "connects_to_id": "c-...", "relationship": "..."}
  ]
}
```

Rules and boundaries:

- `title` and `channel` come from the index; source frontmatter values are retained separately. A disagreement produces a warning and the page shows the index title/channel plus a diagnostic in the build report.
- Summary-frontmatter tags and `insights.json.tags` are unioned case-insensitively for the video's `tags`. Trim and collapse internal whitespace; canonical display name is the lexicographically smallest original spelling by `(casefold, original)`. Preserve no hidden absolute artifact fields.
- Connection endpoint strings join the same concept namespace as tags after the same whitespace/case normalization. Thus a concept may originate only in a connection and have `tag_video_count = 0`.
- Future quote attachment slots are explicitly null. They are not source fields today. A future importer may fill `timestamp_seconds` and `source_url`; rendering must hide controls for null values.
- `verification_status` is derived only as `needed` when `verification_needed` is true and `not-needed` when false. There is no `verified` state in current data and the UI must not imply one.
- Validate all enumerations listed in section 2. Unknown values fail with a clear schema-version error rather than silently disappearing from filters.

### `data/trends.json`

```json
{
  "schema_version": 1,
  "months": ["2021-05", "..."],
  "corpus_growth": [{"month": "2021-05", "published": 1, "cumulative": 1}],
  "tag_monthly": [{"concept_id": "c-...", "name": "ai agents", "counts": [0, 0, 3]}],
  "tag_rankings": [{"concept_id": "c-...", "name": "ai agents", "video_count": 9, "recent_count": 4, "prior_count": 2, "recent_rate": 0.5, "prior_rate": 0.25, "trend_score": 0.25, "rising": true}],
  "insight_type_monthly": [{"month": "2026-08", "total": 42, "counts": {"architecture": 4, "mechanism": 8, "mental_model": 5, "practice": 7, "empirical_result": 3, "failure_mode": 4, "prediction": 6, "tradeoff": 5}}],
  "idea_flow_monthly": [{"month": "2026-08", "article_ideas": 12, "project_ideas": 11, "deep_dives": 14, "open_questions": 13}]
}
```

### `data/concepts.json`

```json
{
  "schema_version": 1,
  "nodes": [{"id": "c-...", "name": "ai agents", "slug": "ai-agents-...", "url": "concepts/ai-agents-.../index.html", "video_ids": ["..."], "tag_video_count": 4, "connection_video_count": 2, "degree": 3, "x": 412, "y": 188}],
  "edges": [{"id": "e-<16hex>", "source": "c-...", "target": "c-...", "relationships": ["..."], "video_ids": ["..."], "occurrence_count": 2}],
  "graph": {"width": 1200, "height": 800, "display_node_ids": ["..."], "display_edge_ids": ["..."]}
}
```

An undirected aggregate edge groups directional source records by sorted endpoint IDs for visualization, while video JSON retains direction. Edge ID is `e-` plus the first 16 SHA-256 hex characters of `min_id + "\n" + max_id`. Self-links remain in detail lists but are omitted from SVG edges.

### `data/ideas.json`, `data/claims.json`, and embedded search records

`ideas.json` has `{schema_version, article_ideas, project_ideas, deep_dives, open_questions}`. `claims.json` has `{schema_version, claims}`. Every aggregate item copies its normalized section fields and adds:

```json
{"video_id": "...", "video_title": "...", "video_url": "videos/.../index.html", "channel": "...", "source_published": "2026-08-01T...Z"}
```

The search page embeds, rather than fetches, an array of records:

```json
{"id": "...", "kind": "video|insight|article|project|deep-dive|question|claim|concept", "title": "...", "text": "...", "tags": ["..."], "channel": "...", "published_date": "YYYY-MM-DD|null", "url": "relative/from/search/page"}
```

Keep each record's `text` to the relevant fields joined with spaces and truncated deterministically to 2,000 Unicode code points. Do not index evidence quotes separately; their text is included in their parent insight/deep-dive text. The JSON is serialized into `<script type="application/json" id="search-data">` with `<` escaped as `\u003c`.

## 5. Derived analytics for Trends

Analytics use the source video's publication month, not ingestion or capture month. Create the continuous month axis from the minimum through maximum `source.published_month`, including zero months. A missing/invalid publication date is a validation error for an analyzed item because temporal pages require it.

### Per-tag monthly counts

For each concept that appears as a video tag, count distinct videos containing it per month. Multiple spellings or duplicate tags in one video count once. Do not count connection-only appearances in tag frequency. Emit zeroes for every month in the shared axis.

### Tag trend ranking and rising concepts

Use a transparent rate-difference heuristic, with no statistics package:

1. Let `active_months` be only months from the first month containing any video through the final month.
2. Define the recent window as the final `min(3, len(active_months))` calendar months.
3. Define the prior window as the immediately preceding `min(3, remaining_months)` calendar months.
4. For a tag, `recent_rate = recent_count / max(1, videos_published_in_recent_window)` and `prior_rate = prior_count / max(1, videos_published_in_prior_window)`.
5. `trend_score = recent_rate - prior_rate`, rounded only for display, not in stored calculation.
6. A concept is `rising` when `recent_count >= 2`, `trend_score > 0`, and it occurs in at least two distinct videos overall.
7. Rank by `(rising desc, trend_score desc, recent_count desc, video_count desc, name.casefold(), name)`.

If there is no prior window, set `prior_count = 0`, `prior_rate = 0`, but label the result “recent activity,” not “rising,” and force `rising = false`. This prevents a tiny/early corpus from manufacturing a trend.

### Insight-type distribution

Count `core_insights` by fixed type order for each publication month. Provide raw counts and total. SVG stacked bars calculate width as `count / total`; a zero-total month renders an empty baseline. The UI may expose percentages computed from the same integers.

### Corpus growth

For each month, count analyzed videos by publication month and compute a cumulative sum in ascending month order. The line chart uses integer SVG coordinates calculated by Python from `(month_index, cumulative/max_cumulative)` with fixed dimensions and margins.

### Idea flow

Count article ideas, project ideas, deep dives, and open questions for each video's publication month. This is a count of generated items, not distinct topics. Display grouped bars or a compact table when the viewport is narrow.

## 6. Page inventory

All page HTML is pre-rendered. Links are computed relative to the current page so opening `site/index.html` via `file://` works. JavaScript enhances but does not create core content.

### Home — `/index.html`

- Purpose: orientation and corpus health at a glance.
- Consumes: `corpus`, latest videos, top/rising concepts, headline trend summaries.
- Regions: privacy/review banner; KPI strip; “recently published” video cards; rising concepts; idea counts; compact corpus-growth SVG; browse entry points; methodology note.

### Trends — `/trends/index.html`

- Purpose: explain corpus evolution without overstating statistical significance.
- Consumes: `trends.json`, concept/video URL maps.
- Regions: time-range summary; corpus growth line; tag-frequency heat/table; rising-concept ranking with formula tooltip; insight-type stacked bars; idea-flow chart; accessible data tables immediately following each SVG.

### Concepts — `/concepts/index.html`

- Purpose: browse vocabulary and observed concept-to-concept relationships.
- Consumes: `concepts.json`.
- Regions: concept search/filter; ranked tag list; deterministic inline SVG graph; graph legend; accessible edge/link list; methodology.
- Graph decision: precompute coordinates in Python and emit inline SVG, with no graph library. Show at most the top 60 nodes ranked by `(-degree, -tag_video_count, name)` and edges whose endpoints are both shown. Place nodes in deterministic concentric rings: rank 0 at center; subsequent rings have capacities 8, 16, 24, …; position by fixed angle `-π/2 + 2π*slot/capacity`; round coordinates to integers. Scale node radius from 5–14 using integer min/max degree. Draw edges first, then linked `<a>` nodes. Vanilla JS may highlight neighbors on focus/hover. Below it, render every concept and every edge as normal links, including those excluded from the SVG.

### Concept detail — `/concepts/<concept-slug>/index.html`

- Purpose: show where one vocabulary term appears and what it connects to.
- Consumes: one concept node, relevant normalized videos, directional connection occurrences.
- Regions: concept header/counts; publication timeline; source-video cards; neighbor list with relationship text and source-video links; related core insights whose video tags include the concept. Do not claim semantic occurrence inside prose unless it came from a tag or connection.

### Ideas — `/ideas/index.html`

- Purpose: make all prospective work browsable.
- Consumes: `ideas.json`.
- Regions: type tabs/anchors; project-idea `fits` filter in fixed order `beyond-evals`, `gatehouse`, `movement-lab`, `new`; article cards; project cards; deep-dive cards ordered by priority high/medium/low then canonical item order; open-question cards; source links on every card. Filtering is progressive enhancement; without JS all groups remain visible.

### Claims — `/claims/index.html`

- Purpose: expose claims and the verification backlog honestly.
- Consumes: `claims.json`.
- Regions: counts by derived verification status; filters `all`, `needed`, `not-needed`; claim-type filter; claim/evidence/question cards; source-video metadata and link; definition note explaining that “not needed” is not “verified.” No `verified` filter exists until the source schema supports it.

### Video detail — `/videos/<video-slug>/index.html` (73 pages currently)

- Purpose: canonical local provenance page for a source video.
- Consumes: one `data/videos/<video_id>.json` object.
- Regions: video header (title, channel, publish date, confidence, review status, privacy badge, YouTube link); rendered summary; tag links; table of contents; all ten structured sections in source order; evidence quote blocks; source footer. Every item has a stable anchor equal to its normalized ID encoded for HTML, and every quote offers only “Open source video” until a real timestamp/source URL exists.

### Search — `/search/index.html?q=...`

- Purpose: corpus-wide client search that also works from a local filesystem.
- Consumes: inline search record JSON embedded in this page.
- Regions: query input; kind filters; result count; ranked result cards; empty state; privacy reminder.
- Algorithm: tokenize the Unicode-casefolded query on non-word boundaries; a record matches when every token occurs in its casefolded title/text/tags/channel haystack. Rank exact title match first, then title prefix, all-token title match, total token occurrence count descending, fixed kind order, title, ID. Debounce input; sync `q` with `history.replaceState` when available.

### Static support files

- `/404.html`: portable not-found guidance with a home link; hosting-specific routing is not assumed.
- `/assets/site.css` and `/assets/site.js`: shared presentation and small enhancements.
- `/data/*.json`: normalized/export/debug contract; pages must remain useful if these cannot be fetched.

### `file://` decision

Core navigation, rendered content, charts, graph, filters, and search must work from `file://`. Browsers commonly block `fetch()` from local files, so search data is embedded in `search/index.html` and page enhancements read DOM or inline JSON. Aggregate JSON remains available for auditing and future HTTP-hosted clients but is not a runtime dependency. The tradeoff is a larger search page and some duplicated data; at this corpus size it is preferable to a local web-server requirement and remains far below the 25 MB budget.

## 7. Design integration contract

Implementation must consume `docs/design-guidelines.md` before writing templates or CSS. The design document owns visual values, while this plan owns stable semantic names and behavior. If a token is absent, add it to the guidelines rather than introducing anonymous one-off CSS values.

Required design tokens, exposed as CSS custom properties with these stable names:

- color: `--color-bg`, `--color-surface`, `--color-surface-raised`, `--color-text`, `--color-text-muted`, `--color-border`, `--color-accent`, `--color-accent-contrast`, `--color-warning`, `--color-danger`, plus `--color-chart-1` through `--color-chart-8`;
- typography: `--font-sans`, `--font-mono`, `--font-size-xs`, `--font-size-sm`, `--font-size-md`, `--font-size-lg`, `--font-size-xl`, `--font-size-2xl`, `--line-height-body`, `--line-height-tight`;
- spacing: `--space-1` through `--space-8`;
- geometry: `--radius-sm`, `--radius-md`, `--radius-lg`, `--shadow-card`, `--content-width`, `--reading-width`, `--nav-height`;
- motion/focus: `--duration-fast`, `--duration-normal`, `--ease-standard`, `--focus-ring`.

Required components/classes and states:

- `site-shell`, `site-header`, `primary-nav`, `breadcrumbs`, `site-footer`;
- `privacy-banner`, `review-badge`, `status-badge`;
- `page-header`, `section-header`, `prose`, `empty-state`;
- `kpi-strip`, `kpi`;
- `card`, `video-card`, `idea-card`, `claim-card`, with default/hover/focus states;
- `tag-pill`, `filter-chip`, `button`, `text-input`, `select`, including selected/disabled/focus states;
- `data-table` with overflow treatment and stacked mobile fallback;
- `trend-chart`, `chart-legend`, `chart-tooltip`, `concept-graph`;
- `video-header`, `source-link`, `metadata-list`, `section-toc`;
- `insight-block`, `quote-block`, `field-pair`, `before-after`;
- `search-form`, `search-result`, `result-kind`;
- `skip-link`, `.visually-hidden`, and visible keyboard focus.

The guidelines must specify light/dark behavior, chart palette contrast, typography hierarchy, max widths, responsive breakpoints, table/graph small-screen behavior, hover and focus behavior, reduced-motion behavior, print behavior, and a clear but non-alarmist private/unreviewed treatment. SVGs require text alternatives and must not encode meaning by color alone.

## 8. Implementation tasks for a Python implementer

Tasks are intentionally ordered for TDD. Do not begin visual implementation until the normalization and integrity contracts pass.

### Task 1 — package and fixture skeleton

Files to create:

- `pyproject.toml`
- `src/yt_insights_web/__init__.py`
- `src/yt_insights_web/cli.py`
- `tests/fixtures/corpus/index.json`
- `tests/fixtures/corpus/artifacts/<3-to-5-real-dirs>/summary.md`
- matching `insights.json` files
- `tests/test_cli.py`

Behavior: define the `build_site = "yt_insights_web.cli:main"` console script and Python/dependency versions. Copy 3–5 real analyzed artifacts: the named 2025-11 sample, one 2026-01 sample, one 2021 sample, and optionally a source with non-`Maxi` channel or null cost. Rewrite fixture index artifact paths to fixture-local paths or relative paths supported by the loader; retain real content and IDs. Do not include transcripts, the whole corpus, credentials, or source Git metadata.

Tests: `--help`, required `--source`, default `--out=site`, bad source path, and no output created on validation failure.

### Task 2 — source loader and validation

Files:

- `src/yt_insights_web/models.py`
- `src/yt_insights_web/load.py`
- `src/yt_insights_web/frontmatter.py`
- `tests/test_load.py`
- `tests/test_frontmatter.py`

Behavior: parse `{items:[...]}`; select only analyzed records; resolve summary and insight files below source artifacts; safely parse YAML/Markdown and JSON; validate required keys, scalar/list types, enum values, video ID agreement in URN/source URI, and ISO dates; produce immutable dataclasses or typed mappings. Accumulate independent validation errors and print source-relative locations. Warnings cover index/frontmatter title or author disagreement. Absolute host paths never enter normalized models.

Tests: happy path across fixture years; malformed root shape; missing artifact; path escape; missing top-level section; wrong nested type; unknown enum; invalid date; frontmatter delimiters; raw HTML disabled; discrepancy warning; skipped/failed excluded.

### Task 3 — normalization and stable identity

Files:

- `src/yt_insights_web/normalize.py`
- `src/yt_insights_web/slug.py`
- `tests/test_normalize.py`
- `tests/test_slug.py`

Behavior: implement the exact video schema, stable IDs, concept normalization, collision-proof slugs, future quote slots, verification-status derivation, and all canonical sorting. Preserve source list order on video objects.

Tests: duplicate/case-varied tags deduplicate; connection-only concept exists; slug punctuation/Unicode/collision behavior; IDs stay stable; quote slots are null; no input artifact path is present in serialized normalized output.

### Task 4 — analytics and aggregates

Files:

- `src/yt_insights_web/derive.py`
- `src/yt_insights_web/graph.py`
- `src/yt_insights_web/search.py`
- `tests/test_derive.py`
- `tests/test_graph.py`
- `tests/test_search.py`

Behavior: implement the formulas and schemas in sections 4–5; continuous month axis; distinct-video tag counts; trend windows/ranking; type and idea monthly counts; concept adjacency; deterministic ring coordinates; bounded search records.

Tests: hand-calculated miniature datasets including empty prior window, zero month, duplicate tag in a video, directed duplicate edges, self-edge, SVG top-60 cutoff, coordinate repeatability, and deterministic search ranking.

### Task 5 — templates, URL helper, and assets

Files:

- `src/yt_insights_web/render.py`
- `src/yt_insights_web/urls.py`
- `src/yt_insights_web/templates/base.html`
- `src/yt_insights_web/templates/components/*.html`
- page templates `home.html`, `trends.html`, `concepts.html`, `concept_detail.html`, `ideas.html`, `claims.html`, `video.html`, `search.html`, `404.html`
- `src/yt_insights_web/static/site.css`
- `src/yt_insights_web/static/site.js`
- `tests/test_urls.py`
- `tests/test_render.py`

Behavior: Jinja autoescape on; render all inventory pages; create relative hrefs from output-page depth and honor base-path configuration for hosted builds; render inline SVG plus accessible tables; embed escaped search JSON; hide unavailable timestamp/external-source controls; JS only enhances existing markup. Implement the stable component/token contract from the design guidelines.

Tests: HTML escaping; Markdown raw HTML suppression; correct relative URLs at root and nested depths; URL prefix behavior; all ten video sections and empty states; every aggregate card has a video link; private banner default; no fake verified/timestamp/source controls; search usable without fetch.

### Task 6 — writer and build transaction

Files:

- `src/yt_insights_web/build.py`
- `src/yt_insights_web/serialize.py`
- additions to `cli.py`
- `tests/test_build.py`

Behavior: render into a temporary sibling of `--out`; emit normalized JSON and copied static assets in deterministic filename order; validate before replacing output. Refuse `--out` if it resolves to the source repository or a parent of it. Existing output replacement must use a sibling backup/rename sequence so a failed build leaves the prior successful output intact; clean only builder-owned temporary paths.

Tests: complete fixture tree; default output; stale generated file removed after successful replacement; failure preserves old output; output/source overlap rejected; two fixture builds have identical file lists and SHA-256 hashes.

### Task 7 — generated-site verifier

Files:

- `src/yt_insights_web/verify.py`
- `tests/test_verify.py`

Behavior: parse every HTML file with a stdlib `html.parser.HTMLParser` collector; resolve internal `href` and `src` values after stripping query/fragment; require target files/directories and fragment IDs to exist; reject absolute filesystem-like text and `file:` URLs; allow only expected outbound `https://www.youtube.com/watch` links; ensure every analyzed video ID has HTML and JSON; enforce the size budget.

Tests: broken page, missing fragment, leaked `/home/...`, Windows drive path, forbidden external URL, missing video page, and oversize tree all fail with useful paths; known-good fixture passes.

### Task 8 — documentation and full-corpus acceptance

Files:

- `README.md`
- `docs/data-contract.md` generated or copied from the finalized schema documentation
- optional `tests/test_real_corpus.py` marked `real_corpus` and skipped unless `YT_INSIGHTS_SOURCE` is set

Behavior: document install/build/open/static-host steps, privacy warning, base-path examples, dependency update method, and the source schema contract. The real-corpus test is read-only and never required for contributors without the private corpus.

Tests: commands below pass; README commands match the actual CLI.

## 9. Verification gates

Run from `/home/rmax-10/src/rmax-ai/yt-insights-explorer`.

### Unit and fixture integration tests

```sh
uv sync
uv run pytest -q
```

Pass means exit 0, no unexpected skips, loader/normalizer/derive/render/verifier coverage included, and no test writes to the real source checkout.

### Full real-corpus build

```sh
uv run build_site --source /home/rmax-10/src/rmax-ai/yt-insights --out .build/site-a
```

Pass means exit 0; 73 video HTML pages and 73 normalized video JSON files; all inventory pages and concept detail pages exist; counts reconcile to the source; output is below 25,000,000 bytes; and the source repository remains byte-for-byte/Git-status unchanged.

### Link, leakage, provenance, and size sanity

```sh
uv run python -m yt_insights_web.verify .build/site-a
```

Pass means every local `href`, `src`, and referenced fragment resolves; all analyzed items have pages; the only allowed outbound content URLs are YouTube watch URLs derived from `source_uri`; no absolute host path, `file:` URL, source artifact pathname, or disallowed external URL occurs; and total generated size is under 25 MB.

Also run explicit leakage scans as defense in depth:

```sh
rg -n '/home/|file:|[A-Za-z]:\\\\' .build/site-a
```

Pass means no matches. If legitimate prose ever contains such text, replace this coarse gate with the verifier's structured allowlist rather than suppressing silently.

### Deterministic output

```sh
uv run build_site --source /home/rmax-10/src/rmax-ai/yt-insights --out .build/site-b
diff -qr .build/site-a .build/site-b
(cd .build/site-a && find . -type f -print0 | sort -z | xargs -0 sha256sum) > .build/site-a.sha256
(cd .build/site-b && find . -type f -print0 | sort -z | xargs -0 sha256sum) > .build/site-b.sha256
diff -u .build/site-a.sha256 .build/site-b.sha256
```

The implementer should provide a small Python manifest command in tests if the null-delimited shell pipeline proves non-portable. Pass means `diff -qr` is empty and corresponding file bytes hash identically; hashes, relative filenames, and file counts must match.

### Browser smoke checks

Open `.build/site-a/index.html` directly and also serve it:

```sh
uv run python -m http.server --directory .build/site-a 8000
```

Pass means root and nested navigation work in both modes; search returns results in `file://` mode without a fetch error; filters and graph focus work; pages remain readable with JavaScript disabled; keyboard focus is visible; narrow viewport tables/graph do not make the whole page overflow; and no console errors occur over HTTP.

## 10. Risks and decisions log

| Decision/risk | Consequence | Graceful handling |
|---|---|---|
| Source `index.json` root is `{items:[...]}` | A bare-list parser would fail or silently misread the corpus. | Validate the exact root and report a schema error; fixture it. |
| No external links exist in insight content | “External sources” cannot currently be offered. | Link every item to its local video provenance and YouTube page. Reserve nullable `source_url` on quote render models; hide empty UI. Do not invent URLs. |
| Evidence quotes have no timestamps | Deep-linking to the relevant moment is impossible. | “Open source video” goes to the base YouTube URL. Reserve nullable `timestamp_seconds`; if later populated, append `t=<seconds>` during normalization and show a timestamp label. |
| Notes are `visibility: private` and `review_status: unreviewed` | A static host can accidentally disclose them; static files cannot enforce access control. | Default to private mode with persistent banner and `robots` noindex metadata. Build does not deploy. Public mode requires both `--publication public` and, while such records remain, `--acknowledge-private-unreviewed`; hosting access control is outside this site. |
| Index stores absolute artifact paths | They could leak host identity and break on another checkout. | Use paths only for validated reads below the selected source root; normalized models and pages contain none. Verifier rejects leaks. |
| Tags and connection endpoints have free-text spelling | Naive grouping can split concepts; aggressive stemming can merge distinct concepts. | Apply only trim, whitespace collapse, and Unicode casefold equality. Preserve a deterministic display spelling; no stemming or fuzzy merge. |
| Connection graph can become visually dense | Rendering all ~500 concepts as SVG would be unreadable. | Cap only the visual overview at 60 deterministically; retain the complete accessible concept and connection link lists and all detail pages. |
| Trend windows cover sparse historical data | Counts could look more rigorous than they are. | Use simple documented rate differences, show raw numerator/denominator, require two recent videos, and label no-baseline cases as activity rather than rising. |
| `file://` blocks `fetch()` in common browsers | A JSON-fetched search UI would fail locally. | Embed the bounded search index in the search page and pre-render core content; keep JSON as an audit/export artifact only. |
| Source schema may evolve | Silent field loss would misrepresent the corpus. | Version normalized schema; fail on missing required keys and unknown enum values; add migration logic and fixtures deliberately. |
| Summary Markdown is generated content | Raw HTML could inject unwanted markup or scripts into a published build. | Disable raw HTML in Markdown, autoescape templates, escape embedded JSON, and permit only builder-authored markup. |
| Base-path and relative-link requirements can conflict | Root-absolute links work on a host but fail locally. | Internal links are relative by default; `--base-path` is used for canonical/host metadata and explicitly tested. Never require `<base href>`. |
| Generated output replacement can destroy a prior good build | A late validation error could leave a partial site. | Build and verify in a sibling temporary directory, then rename into place; reject overlapping source/output paths. |
| Publication date differs from ingestion date | “Corpus growth” can mean source chronology or capture chronology. | Trends explicitly use source publication month; show captured/ingested metadata on video pages but do not mix it into trend charts. |

## 11. Acceptance summary

The first release is complete when the verification gates pass on both fixtures and all 73 analyzed artifacts, every structured item is reachable through a video page, concepts/ideas/claims/trends are browsable without a backend, search works from `file://`, no absolute source path or unsupported citation is emitted, two builds are byte-identical, output remains below 25 MB, and private/unreviewed status is impossible to miss.
