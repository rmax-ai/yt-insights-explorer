from __future__ import annotations

from yt_insights_web.urls import (
    asset_url,
    normalize_base_path,
    page_url,
    relative_url,
)


def test_relative_urls_work_from_root_and_nested_pages() -> None:
    assert relative_url("index.html", "trends/index.html") == "trends/index.html"
    assert relative_url("trends/index.html", "index.html") == "../index.html"
    assert (
        relative_url("videos/example/index.html", "concepts/index.html")
        == "../../concepts/index.html"
    )


def test_base_path_is_normalized_for_hosted_assets() -> None:
    assert normalize_base_path("./") == "./"
    assert normalize_base_path("/yt-insights") == "/yt-insights/"
    assert page_url("index.html", "/yt-insights/") == "/yt-insights/index.html"
    assert (
        asset_url("trends/index.html", "assets/site.css", "/yt-insights/")
        == "/yt-insights/assets/site.css"
    )
