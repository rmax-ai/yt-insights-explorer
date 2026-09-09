from __future__ import annotations

from pathlib import Path

from yt_insights_web.load import load_corpus
from yt_insights_web.normalize import normalize_corpus
from yt_insights_web.render import RenderConfig, render_site

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "corpus"


def rendered_fixture(**kwargs: str) -> dict[str, str]:
    normalized = normalize_corpus(load_corpus(FIXTURE))
    return render_site(normalized, RenderConfig(**kwargs))


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
