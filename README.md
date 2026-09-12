# YT Insights Explorer

YT Insights Explorer builds a deterministic, backend-free static site from the
analyzed artifacts in a read-only `yt-insights` checkout. It emits ordinary
HTML, CSS, JavaScript, and JSON. The site has no accounts, API, analytics,
external webfonts, or tracking.

## Install

Python 3.12 or newer and [uv](https://docs.astral.sh/uv/) are required.

```sh
uv sync
```

Dependencies are declared in `pyproject.toml` and locked in `uv.lock`. To
refresh them deliberately, edit the dependency declaration and run
`uv lock`, then run `uv sync`. Do not install system packages.

## Build

The source checkout is input only. The builder never writes to it and does not
copy transcripts.

```sh
uv run build_site \
  --source "$HOME/src/rmax-ai/yt-insights" \
  --out site
```

The default output is `site/`. Other useful options are:

```sh
# Build links for a site hosted below https://example.com/yt-insights/
uv run build_site \
  --source "$HOME/src/rmax-ai/yt-insights" \
  --out site \
  --base-path /yt-insights/

# Change the visible title
uv run build_site \
  --source "$HOME/src/rmax-ai/yt-insights" \
  --out site \
  --site-title "My Insights"

# Explicitly acknowledge publication of private/unreviewed records
uv run build_site \
  --source "$HOME/src/rmax-ai/yt-insights" \
  --out site \
  --publication public \
  --acknowledge-private-unreviewed
```

Public mode changes the robots treatment and warning banner. It does not add
access control or deploy anything. A static host must be protected separately.

## View and serve

For a quick local preview, open `site/index.html` directly. Core navigation,
pre-rendered content, charts, filters, and the embedded search index do not
depend on a server. A local server is useful for checking normal HTTP hosting:

```sh
uv run python -m http.server --directory site 8000
```

Visit <http://localhost:8000/>. Nested pages use relative links, so the same
tree can be copied to a static host. For a host prefix, build with
`--base-path /yt-insights/`.

## Publish to GitHub Pages

The live site is the `gh-pages` branch of this repository, served on the
custom domain at <https://yt-insights-explorer.rmax.ai/> (served at the domain
root, base path `/`). It is generated content; the branch is force-pushed on
each refresh.

```sh
rm -rf .build/site-public
uv run build_site \
  --source "$HOME/src/rmax-ai/yt-insights" \
  --out .build/site-public \
  --publication public \
  --acknowledge-private-unreviewed \
  --base-path /
touch .build/site-public/.nojekyll
printf 'yt-insights-explorer.rmax.ai\n' > .build/site-public/CNAME
git -C .build/site-public init -q -b gh-pages
git -C .build/site-public add -A
git -C .build/site-public -c user.name="YT Insights" -c user.email="yt-insights@localhost" commit -q -m "site: refresh $(date -u +%F)"
git -C .build/site-public push -f https://github.com/rmax-ai/yt-insights-explorer.git HEAD:gh-pages
```

Public mode flips the robots treatment to `index, follow` and relaxes the
warning banner. The corpus remains unreviewed research notes: publish only
when that is the intent, and consider the claims page a verification backlog,
not a set of verified statements.

## Verification

The normal contributor checks are:

```sh
uv sync
uv run pytest -q
uv run build_site --source "$HOME/src/rmax-ai/yt-insights" --out .build/site-a
uv run python -m yt_insights_web.verify .build/site-a
uvx ruff check src/ tests/
```

The build is transactional: it renders and verifies a sibling temporary tree
before replacing the previous output. Generated directories under `.build/`
and `site/` are ignored by Git.

## Privacy and provenance

The default publication mode is private. Every page displays the
private/unreviewed treatment, and private builds emit `noindex, nofollow`.
The output is still static content, so a host can expose it if access control
is not configured. Treat a successful build as generation, not publication.

Every extracted idea, claim, insight, and connection links back to a local
video detail page. Video pages retain the source title, channel, publication
date, review metadata, and the original YouTube watch URL. No transcript is
rendered or copied. The generated tree must not contain source checkout paths.

## Source contract

The source root must contain an `index.json` object with an `items` array.
Only `status: analyzed` records are included. Each analyzed record points to a
`summary.md` and `insights.json` beneath the source `artifacts/` directory.
Summaries use YAML frontmatter followed by Markdown. The loader validates
required fields, dates, nested section types, and the controlled enum values.
Skipped and failed index records remain in corpus counts but do not become
video pages.

The generated JSON contract is documented in
[`docs/data-contract.md`](docs/data-contract.md).

## Overlay inputs

The compiler discovers optional curation overlays relative to the source
checkout, not relative to the generated site:

```text
<source-root>/corpus/concepts.yml
<source-root>/corpus/topics.yml
<source-root>/corpus/projects.yml
<source-root>/corpus/claim-verification.json
```

Missing overlay files mean that optional enrichment is absent. Empty,
schema-versioned documents are valid and mean that the corresponding overlay
is intentionally empty. In both cases the legacy build remains byte-identical.
Raw labels, source locations, and unresolved status are retained internally;
the current build projection does not expose resolved overlay fields.

Overlay application is deterministic: concepts and topics are resolved first,
projects second, and claim reviews last. Present-but-malformed documents fail
closed with the overlay path, entry or review ID, and occurrence that caused
the error. This includes unsupported versions, malformed envelopes, duplicate
IDs, dangling claim references, invalid supersession graphs, conflicting active
reviews, and fingerprint mismatches. Unknown labels and absent review entries
are optional enrichment and remain unresolved rather than aborting unrelated
records. Claim staleness is derived only from the committed
`claim-verification.json` policy cutoff, never from the build clock.

## Development notes

The runtime site is dependency-free vanilla JavaScript. Python owns
normalization, sorting, analytics, SVG coordinates, rendering, serialization,
and verification. Avoid adding a client framework or runtime fetch dependency:
the local-first behavior is intentional.
