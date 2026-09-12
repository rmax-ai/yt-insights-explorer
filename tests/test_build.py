from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from yt_insights_web.build import BuildError, _compact_html, build_site
from yt_insights_web.corpus import compiler
from yt_insights_web.load import load_corpus
from yt_insights_web.normalize import normalize_corpus
from yt_insights_web.slug import concept_id

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "corpus"
V1_EDGE_FIXTURE = ROOT / "tests" / "fixtures" / "v1-edge"
V1_EDGE_MANIFEST = ROOT / "tests" / "fixtures" / "golden" / "v1-edge-site-manifest.json"


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


def test_build_invokes_the_compiler(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[object, ...]] = []
    original = compiler.compile_corpus

    def spy(records: object, overlays: object | None = None):
        calls.append(tuple(records))  # type: ignore[arg-type]
        return original(records, overlays=overlays)  # type: ignore[arg-type]

    monkeypatch.setattr(compiler, "compile_corpus", spy)

    build_site(FIXTURE, tmp_path / "site")

    assert len(calls) == 1
    assert len(calls[0]) == 3


def test_routed_v1_build_matches_e0_site_manifest(tmp_path: Path) -> None:
    output = tmp_path / "site"

    build_site(V1_EDGE_FIXTURE, output, generated_at=None)

    assert manifest(output) == json.loads(V1_EDGE_MANIFEST.read_text(encoding="utf-8"))


def test_compiler_projection_preserves_v1_identity_order_and_evidence() -> None:
    loaded = load_corpus(V1_EDGE_FIXTURE)
    compiled = compiler.compile_corpus(loaded.videos)
    normalized = normalize_corpus(loaded)

    projected_by_id = {video["video_id"]: video for video in normalized.videos}
    for compiled_video in compiled.videos:
        projected = projected_by_id[compiled_video.video_id]
        assert projected["video_id"] == compiled_video.video_id
        for section in (
            "core_insights",
            "deep_dives",
            "article_ideas",
            "project_ideas",
            "architectural_implications",
            "tradeoffs_and_failure_modes",
            "open_questions",
            "key_claims",
            "connections",
        ):
            typed_items = getattr(compiled_video, section)
            public_items = projected[section]
            assert [item["id"] for item in public_items] == [item.id for item in typed_items]
            assert [item["source_index"] for item in public_items] == [
                item.source_index for item in typed_items
            ]

        assert [
            quote["text"]
            for item in projected["core_insights"]
            for quote in item["evidence_quotes"]
        ] == [
            evidence.text
            for item in compiled_video.core_insights
            for evidence in item.evidence
        ]
        assert [tag["concept_id"] for tag in projected["tags"]] == [
            concept_id(tag["name"]) for tag in projected["tags"]
        ]
        assert all(
            connection["concept_id"] == concept_id(connection["concept"])
            and connection["connects_to_id"] == concept_id(connection["connects_to"])
            for connection in projected["connections"]
        )


def test_build_does_not_read_overlay_or_ledger_registry_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    forbidden_names = {
        "claim-verification.json",
        "concepts.yml",
        "projects.yml",
        "topics.yml",
    }
    original_read_text = Path.read_text

    def read_text(path: Path, *args: object, **kwargs: object) -> str:
        if path.name in forbidden_names:
            raise AssertionError(f"unexpected overlay or ledger read: {path}")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)

    build_site(FIXTURE, tmp_path / "site")
