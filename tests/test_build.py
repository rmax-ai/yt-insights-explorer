from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from yt_insights_web.build import BuildError, _compact_html, build_site

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "corpus"


def manifest(path: Path) -> dict[str, str]:
    return {
        file.relative_to(path).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in sorted(path.rglob("*"))
        if file.is_file()
    }


def test_build_writes_complete_fixture_tree(tmp_path: Path) -> None:
    output = tmp_path / "site"

    build_site(FIXTURE, output)

    assert (output / "index.html").is_file()
    assert (output / "data" / "corpus.json").is_file()
    assert len(list((output / "data" / "videos").glob("*.json"))) == 3
    assert len(list((output / "videos").glob("*/index.html"))) == 3


def test_successful_build_removes_stale_generated_files(tmp_path: Path) -> None:
    output = tmp_path / "site"
    build_site(FIXTURE, output)
    stale = output / "stale.txt"
    stale.write_text("stale", encoding="utf-8")

    build_site(FIXTURE, output)

    assert not stale.exists()


def test_failed_build_preserves_previous_output(tmp_path: Path) -> None:
    output = tmp_path / "site"
    build_site(FIXTURE, output)
    before = manifest(output)
    broken = tmp_path / "broken"
    shutil.copytree(FIXTURE, broken)
    (broken / "index.json").write_text("[]", encoding="utf-8")

    with pytest.raises(BuildError):
        build_site(broken, output)

    assert manifest(output) == before


def test_source_output_overlap_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(BuildError, match="overlap"):
        build_site(FIXTURE, FIXTURE)


def test_two_fixture_builds_are_byte_identical(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    build_site(FIXTURE, first)
    build_site(FIXTURE, second)

    assert manifest(first) == manifest(second)


def test_compact_html_preserves_code_and_embedded_json() -> None:
    source = (
        "<div>\n  alpha   beta\n</div>"
        "<pre>  alpha\n  beta</pre>"
        '<script type="application/json">{"text":"a  b"}</script>'
    )

    compact = _compact_html(source)

    assert "<div> alpha beta </div>" in compact
    assert "<pre>  alpha\n  beta</pre>" in compact
    assert '{"text":"a  b"}' in compact
