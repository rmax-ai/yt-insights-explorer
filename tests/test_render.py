from __future__ import annotations

import json
from pathlib import Path

from yt_insights_web import render as render_module
from yt_insights_web.derive import month_axis
from yt_insights_web.load import load_corpus
from yt_insights_web.normalize import normalize_corpus
from yt_insights_web.render import RenderConfig, render_site

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "corpus"


def rendered_fixture(**kwargs: str) -> dict[str, str]:
    normalized = normalize_corpus(load_corpus(FIXTURE))
    return render_site(normalized, RenderConfig(**kwargs))


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
