from __future__ import annotations

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
    page.write_text(page.read_text(encoding="utf-8") + text, encoding="utf-8")

    with pytest.raises(VerificationError, match="filesystem"):
        verify_site(built_site)


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
