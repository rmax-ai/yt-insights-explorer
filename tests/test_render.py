from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from yt_insights_web import render as render_module
from yt_insights_web.corpus.compiler import compile_corpus
from yt_insights_web.corpus.normalized_models import EvidenceAvailability
from yt_insights_web.derive import month_axis
from yt_insights_web.load import load_corpus
from yt_insights_web.normalize import normalize_corpus
from yt_insights_web.render import RenderConfig, render_site

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "corpus"
RENDER_FIXTURE = ROOT / "tests" / "fixtures" / "render-evidence"


def rendered_fixture(**kwargs: str) -> dict[str, str]:
    normalized = normalize_corpus(load_corpus(FIXTURE))
    return render_site(normalized, RenderConfig(**kwargs))


def rendered_evidence_fixture() -> dict[str, str]:
    loaded = load_corpus(RENDER_FIXTURE)
    canonical = compile_corpus(loaded.videos, overlay_root=RENDER_FIXTURE)
    return render_site(canonical)


def evidence_video_html(files: dict[str, str]) -> str:
    return files["videos/render-evidence-fixture-render-evidence-001/index.html"]


def _legacy_timeline(concept_id: str, videos: tuple[dict, ...]) -> list[dict]:
    months = month_axis(list(videos))
    rows = []
    for month in months:
        count = sum(
            any(tag["concept_id"] == concept_id for tag in video["tags"])
            or any(
                connection["concept_id"] == concept_id
                or connection["connects_to_id"] == concept_id
                for connection in video["connections"]
            )
            for video in videos
            if video["source"]["published_month"] == month
        )
        if count:
            rows.append({"month": month, "count": count})
    return rows


def test_fixture_timelines_match_distinct_video_reference_semantics() -> None:
    normalized = normalize_corpus(load_corpus(FIXTURE))
    months = month_axis(list(normalized.videos))
    histogram = render_module._timeline_histogram(normalized.videos)

    for concept in normalized.concepts:
        assert render_module._timeline(concept["id"], months, histogram) == _legacy_timeline(
            concept["id"], normalized.videos
        )


def test_render_site_contains_inventory_and_video_sections() -> None:
    files = rendered_fixture()
    video_path = next(
        path for path in files if path.startswith("videos/") and path != "videos/index.html"
    )
    video_html = files[video_path]

    assert "privacy-banner" in files["index.html"]
    assert "core-insights" in video_html
    assert "deep-dives" in video_html
    assert "article-ideas" in video_html
    assert "project-ideas" in video_html
    assert "architectural-implications" in video_html
    assert "tradeoffs-and-failure-modes" in video_html
    assert "open-questions" in video_html
    assert "key-claims" in video_html
    assert "connections" in video_html
    assert 'id="search-data"' in files["search/index.html"]
    assert "<script>" not in files["search/index.html"]
    search_json = (
        files["search/index.html"]
        .split('<script type="application/json" id="search-data">')[1]
        .split("</script>", 1)[0]
    )
    search_records = json.loads(search_json)
    assert all(len(record["text"]) <= 2000 for record in search_records)
    assert all("published_date" in record for record in search_records)


def test_rendered_paths_and_relative_links_are_local() -> None:
    files = rendered_fixture(base_path="/yt-insights/")

    assert "index.html" in files
    assert "trends/index.html" in files
    assert "concepts/index.html" in files
    assert "ideas/index.html" in files
    assert "claims/index.html" in files
    assert "videos/index.html" in files
    assert "404.html" in files
    assert 'href="/yt-insights/trends/index.html"' in files["index.html"]


def test_render_autoescapes_values() -> None:
    normalized = normalize_corpus(load_corpus(FIXTURE))
    mutated = dict(normalized.videos[0])
    mutated["title"] = '<script>alert("x")</script>'
    normalized = normalized.__class__(
        videos=(mutated, *normalized.videos[1:]),
        concepts=normalized.concepts,
        index_items=normalized.index_items,
        warnings=normalized.warnings,
    )
    files = render_site(normalized)

    assert "&lt;script&gt;" in files["index.html"]
    assert '<script>alert("x")</script>' not in files["index.html"]


def test_timestamped_evidence_renders_youtube_deep_link() -> None:
    html = evidence_video_html(rendered_evidence_fixture())

    assert (
        'href="https://www.youtube.com/watch?v=render-evidence-001&amp;t=37s"'
        in html
    )
    assert "at 37s" in html


def test_missing_timestamp_renders_normal_source_link() -> None:
    html = evidence_video_html(rendered_evidence_fixture())

    assert (
        'href="https://www.youtube.com/watch?v=render-evidence-001"'
        in html
    )
    assert "?t=" not in html


def test_unavailable_enrichment_does_not_drop_item() -> None:
    html = evidence_video_html(rendered_evidence_fixture())

    assert "Unavailable enrichment does not erase an insight." in html
    assert (
        "This &lt;insight&gt; remains present while enrichment is unavailable "
        "&amp; auditable."
    ) in html
    assert "Evidence Unavailable" in html


@pytest.mark.parametrize(
    "availability",
    [EvidenceAvailability.CANDIDATE, EvidenceAvailability.UNRESOLVED],
)
def test_fuzzy_or_unresolved_candidates_remain_untimed(availability) -> None:
    loaded = load_corpus(RENDER_FIXTURE)
    canonical = compile_corpus(loaded.videos, overlay_root=RENDER_FIXTURE)
    video = canonical.videos[0]
    insight = video.core_insights[1]
    evidence = replace(
        insight.evidence[0],
        availability=availability,
        resolution_method="summary_quote_fuzzy",
    )
    changed_insight = replace(insight, evidence=(evidence,))
    changed_video = replace(video, core_insights=(video.core_insights[0], changed_insight))
    changed_corpus = replace(canonical, videos=(changed_video,))

    html = evidence_video_html(render_site(changed_corpus))

    state = availability.value.title()
    marker = f'data-evidence-availability="{availability.value}"'
    block = html.split(marker, 1)[1].split("</figure>", 1)[0]
    assert f"Evidence {state}" in block
    assert "at " not in block
    assert "?t=" not in block


@pytest.mark.parametrize(
    ("claim_id", "requested", "review"),
    [
        ("requested-verified", "Verification requested", "Ledger review: verified"),
        ("requested-unreviewed", "Verification requested", "Ledger review: unreviewed"),
        ("unrequested-verified", "Verification not requested", "Ledger review: verified"),
        ("unrequested-unreviewed", "Verification not requested", "Ledger review: unreviewed"),
    ],
)
def test_claim_request_and_review_states_render_separately(
    claim_id: str,
    requested: str,
    review: str,
) -> None:
    files = rendered_evidence_fixture()
    video_html = evidence_video_html(files)
    claims_html = files["claims/index.html"]
    anchor = f'id="claim-v1:render-evidence-001:{claim_id}"'

    for html in (claims_html, video_html):
        card = html.split(anchor, 1)[1].split("</article>", 1)[0]
        assert requested in card
        assert review in card
