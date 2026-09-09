# Generated data contract

The builder emits schema version `1` JSON below `data/`. All JSON is UTF-8
with LF endings, lexical JSON keys, compact separators, `ensure_ascii=false`,
and one final newline. Values are derived from the normalized in-memory corpus;
the files are export and audit artifacts, not a runtime dependency of the
pre-rendered pages.

## `data/corpus.json`

```json
{
  "schema_version": 1,
  "site": {
    "title": "YT Insights Explorer",
    "publication_mode": "private",
    "base_path": "./"
  },
  "counts": {},
  "total_cost_usd": 0,
  "video_ids": [],
  "months": []
}
```

`counts` includes index items, analyzed/skipped/failed videos, concepts, core
insights, article ideas, project ideas, deep dives, open questions, and claims.
`video_ids` is ordered by source publication descending and video ID ascending.
`months` is the continuous `YYYY-MM` publication axis. A supplied
`--generated-at` value is optional display metadata and is omitted by default.

## `data/videos/<video_id>.json`

Each analyzed video contains:

- identity: `schema_version`, `video_id`, `slug`, `url`, `title`, `channel`,
  `status`, `ingested_at`, and `cost_usd_total`;
- `source`: YouTube type and URI, source title/author, and UTC
  `published_at`, `published_date`, and `published_month`;
- `document`: frontmatter type, description, URN, completion status,
  confidence, visibility, captured time, generator, and review status;
- `summary`: the source Markdown body and safe HTML rendered with raw HTML
  disabled;
- `tags`: canonical display name, concept ID, and relative concept URL;
- all extracted sections: `core_insights`, `deep_dives`, `article_ideas`,
  `project_ideas`, `architectural_implications`,
  `tradeoffs_and_failure_modes`, `open_questions`, `key_claims`, and
  `connections`.

Each section item receives a stable ID:

```text
<video_id>:<section>:<zero-based source_index>
```

Source list order is retained on video pages. Every item also has
`source_index`. Evidence quote slots are explicit:
`timestamp_seconds` and `source_url` are `null` unless a future importer
provides real values. No timestamps are invented.

Tags and connection endpoints share one namespace. Equality means only
trimmed, internal-whitespace-collapsed, Unicode-case-insensitive equality.
There is no stemming or fuzzy merge. Concept IDs are
`c-<slug>-<first-eight-lowercase-SHA-256-hex>` over the canonical name.

For key claims, `verification_status` is derived from the Boolean
`verification_needed` field. It is `needed` when true and `not-needed` when
false. `not-needed` does not mean verified; the current source schema has no
verified state.

No source artifact pathname, absolute host path, hostname, branch, build
clock, or random value is serialized.

## Aggregate files

### `data/trends.json`

Contains the continuous month axis, corpus growth, tag monthly counts and
rankings, insight-type monthly counts, and idea-flow monthly counts. Trend
rates use the final three calendar months and the immediately preceding three
calendar months, or the available shorter windows. A tag is `rising` only if
it appears in at least two videos, has at least two recent videos, and has a
positive recent-minus-prior rate difference with a prior window.

Tag monthly counts are distinct videos per month. Insight type order is fixed
to architecture, mechanism, mental model, practice, empirical result,
failure mode, prediction, and tradeoff.

### `data/concepts.json`

Contains every concept node and every aggregate connection edge. Nodes include
tag/connection video counts, degree, and deterministic SVG `x`/`y`
coordinates. Directional connection records are retained on video pages.
Undirected edge IDs use the first 16 SHA-256 hex characters of the sorted
endpoint IDs. Self-links are retained in detail data but omitted from SVG
edges. The visual overview is capped at the top 60 nodes; accessible lists
retain everything.

### `data/ideas.json`

Contains `article_ideas`, `project_ideas`, `deep_dives`, and
`open_questions`. Each item copies normalized fields and adds
`video_id`, `video_title`, `video_url`, `channel`, and `source_published`.

### `data/claims.json`

Contains `claims` with the same provenance fields and the normalized
verification status. Claim types are causal, comparative, factual, opinion,
and prediction.

### `data/search.json`

Contains the bounded records embedded into the Search page. Records have
`id`, `kind`, `title`, `text`, `tags`, `channel`, `published_date`, and a
relative URL from `search/index.html`. Text is truncated to 2,000 Unicode code
points. Evidence quotes are included in their parent insight or deep-dive
record and are not indexed separately.
