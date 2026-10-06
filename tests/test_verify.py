from __future__ import annotations

import json
from pathlib import Path

import pytest

from yt_insights_web.build import build_site
from yt_insights_web.verify import VerificationError, verify_site

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "corpus"


@pytest.fixture
def built_site(tmp_path: Path) -> Path:
    output = tmp_path / "site"
    build_site(FIXTURE, output)
    return output


def test_known_good_tree_passes(built_site: Path) -> None:
    report = verify_site(built_site)

    assert report.html_files >= 8
    assert report.total_bytes > 0


def test_broken_page_link_fails(built_site: Path) -> None:
    page = built_site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace('href="trends/index.html"', 'href="missing.html"'),
        encoding="utf-8",
    )

    with pytest.raises(VerificationError, match="missing.html"):
        verify_site(built_site)


def test_internal_link_escape_fails(built_site: Path) -> None:
    page = built_site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8") + '<a href="../outside.html">bad</a>',
        encoding="utf-8",
    )

    with pytest.raises(VerificationError, match="internal link escapes generated tree"):
        verify_site(built_site)


def test_directory_target_maps_to_index(built_site: Path) -> None:
    page = built_site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8") + '<a href="trends/">trends</a>',
        encoding="utf-8",
    )

    verify_site(built_site)


def test_existing_fragment_passes(built_site: Path) -> None:
    page = built_site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8") + '<a href="trends/index.html#main">trends</a>',
        encoding="utf-8",
    )

    verify_site(built_site)


def test_missing_fragment_fails(built_site: Path) -> None:
    page = built_site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            'href="trends/index.html"', 'href="trends/index.html#missing"'
        ),
        encoding="utf-8",
    )

    with pytest.raises(VerificationError, match="fragment"):
        verify_site(built_site)


def _insert_before_body_end(page: Path, text: str) -> None:
    contents = page.read_text(encoding="utf-8")
    page.write_text(contents.replace("</body>", f"{text}</body>", 1), encoding="utf-8")


@pytest.mark.parametrize(
    "text",
    [
        "/home/rmax/private",
        "file:///tmp/secret",
        r"C:\Users\secret",
    ],
)
def test_filesystem_leaks_fail(built_site: Path, text: str) -> None:
    page = built_site / "index.html"
    _insert_before_body_end(page, text)

    with pytest.raises(VerificationError, match="filesystem path leak in"):
        verify_site(built_site)


def _write_video_json_string(built_site: Path, value: str) -> Path:
    video_json = sorted((built_site / "data" / "videos").glob("*.json"))[0]
    data = json.loads(video_json.read_text(encoding="utf-8"))
    data["leak_regression"] = value
    with video_json.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
    return video_json


def test_power_profile_prose_passes(built_site: Path) -> None:
    _insert_before_body_end(
        built_site / "index.html",
        "the power profile: about 15W",
    )

    verify_site(built_site)


def test_json_multiline_list_passes(built_site: Path) -> None:
    _write_video_json_string(
        built_site,
        "Components:\n  - Vehicle Intelligence\n  - Vehicle OS",
    )

    verify_site(built_site)


def test_json_escaped_quote_passes(built_site: Path) -> None:
    _write_video_json_string(built_site, 'Yang: "Claude"')

    verify_site(built_site)


def test_json_windows_path_fails(built_site: Path) -> None:
    video_json = _write_video_json_string(built_site, r"C:\Users\secret")

    with pytest.raises(VerificationError) as exc_info:
        verify_site(built_site)
    assert str(exc_info.value) == f"filesystem path leak in {video_json}"


def test_forbidden_external_url_fails(built_site: Path) -> None:
    page = built_site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8") + '<a href="https://example.com/">bad</a>',
        encoding="utf-8",
    )

    with pytest.raises(VerificationError, match="external"):
        verify_site(built_site)


def test_missing_video_page_or_json_fails(built_site: Path) -> None:
    video_json = next((built_site / "data" / "videos").glob("*.json"))
    video_json.unlink()

    with pytest.raises(VerificationError, match="video"):
        verify_site(built_site)


def test_oversize_tree_fails(built_site: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from yt_insights_web import verify as verify_module

    monkeypatch.setattr(verify_module, "MAX_BYTES", 1_024)

    with pytest.raises(VerificationError, match="over "):
        verify_site(built_site)
