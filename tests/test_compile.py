from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from yt_insights_web.build import BuildError, build_site
from yt_insights_web.corpus.compiler import OVERLAY_APPLICATION_ORDER, compile_corpus
from yt_insights_web.corpus.identity import claim_fingerprint
from yt_insights_web.corpus.registries import RegistryValidationError
from yt_insights_web.load import load_corpus
from yt_insights_web.normalize import normalize_corpus

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "v1-edge"
GOLDEN = ROOT / "tests" / "fixtures" / "golden" / "v1-edge-site-manifest.json"
VIDEO_ID = "V1Edge9xYzA"
CLAIM_REF = f"claim:{VIDEO_ID}:key_claims:0"


def _manifest(path: Path) -> dict[str, str]:
    return {
        file.relative_to(path).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in sorted(path.rglob("*"))
        if file.is_file()
    }


def _empty_documents() -> dict[str, object]:
    return {
        "concepts": {"schema_version": 1, "concepts": []},
        "topics": {"schema_version": 1, "topics": []},
        "projects": {"schema_version": 1, "projects": []},
        "claims": {"schema_version": 1, "reviews": []},
    }


def _populated_documents() -> dict[str, object]:
    return {
        "concepts": {
            "schema_version": 1,
            "concepts": [
                {
                    "id": "concept_multilingual",
                    "canonical_name": "Multilingual systems",
                    "aliases": ["多言語"],
                    "status": "retired",
                }
            ],
        },
        "topics": {
            "schema_version": 1,
            "topics": [
                {
                    "id": "topic_systems",
                    "canonical_name": "Systems",
                    "aliases": [],
                    "status": "canonical",
                }
            ],
        },
        "projects": {
            "schema_version": 1,
            "projects": [
                {
                    "id": "project_movement_lab",
                    "canonical_name": "Movement Lab",
                    "aliases": ["movement-lab"],
                    "status": "active",
                }
            ],
        },
        "claims": {
            "schema_version": 1,
            "reviews": [
                {
                    "id": "review:compile",
                    "claim_refs": [CLAIM_REF],
                    "claim_fingerprint": claim_fingerprint(
                        "A V1 baseline must preserve unavailable verification context."
                    ),
                    "status": "verified",
                    "method": "primary-source review",
                    "evidence": [{"url": "https://example.test", "note": "checked"}],
                    "reviewed_at": "2026-09-11T00:00:00Z",
                    "reviewer": "compiler test",
                }
            ],
        },
    }


def _write_overlays(source: Path, documents: dict[str, object]) -> None:
    corpus = source / "corpus"
    corpus.mkdir()
    concepts = documents["concepts"]["concepts"]  # type: ignore[index]
    concepts_yaml = "schema_version: 1\nconcepts:"
    if concepts:
        concepts_yaml += "\n" + "".join(
            f"\n  - id: {entry['id']}\n"
            f"    canonical_name: {entry['canonical_name']}\n"
            f"    aliases: {entry['aliases']}\n"
            f"    status: {entry['status']}"
            for entry in concepts
        )
    else:
        concepts_yaml += " []"
    (corpus / "concepts.yml").write_text(
        concepts_yaml + "\n",
        encoding="utf-8",
    )
    (corpus / "topics.yml").write_text(
        "schema_version: 1\ntopics: []\n",
        encoding="utf-8",
    )
    (corpus / "projects.yml").write_text(
        "schema_version: 1\nprojects: []\n",
        encoding="utf-8",
    )
    (corpus / "claim-verification.json").write_text(
        json.dumps(documents["claims"], ensure_ascii=False),
        encoding="utf-8",
    )


def test_empty_overlays_match_pre_overlay_golden(tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(FIXTURE, source)
    _write_overlays(source, _empty_documents())
    output = tmp_path / "site"

    build_site(source, output, generated_at=None)

    assert _manifest(output) == json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_overlay_application_order_is_documented_and_applied() -> None:
    loaded = load_corpus(FIXTURE)
    compiled = compile_corpus(loaded.videos, overlays=_populated_documents())

    assert OVERLAY_APPLICATION_ORDER == ("concepts/topics", "projects", "claim reviews")
    assert compiled.resolved_state.application_order == OVERLAY_APPLICATION_ORDER  # type: ignore[union-attr]
    assert compiled.resolved_state.claims[0].status == "verified"  # type: ignore[union-attr]


def test_populated_overlay_repeat_build_is_byte_identical(tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(FIXTURE, source)
    _write_overlays(source, _populated_documents())
    first = tmp_path / "first"
    second = tmp_path / "second"

    build_site(source, first, generated_at=None)
    build_site(source, second, generated_at=None)

    assert _manifest(first) == _manifest(second)
    assert _manifest(source) == _manifest(source)


def test_missing_and_empty_overlays_are_both_valid(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    empty = tmp_path / "empty"
    shutil.copytree(FIXTURE, missing)
    shutil.copytree(FIXTURE, empty)
    _write_overlays(empty, _empty_documents())

    missing_result = normalize_corpus(load_corpus(missing), overlay_root=missing)
    empty_result = normalize_corpus(load_corpus(empty), overlay_root=empty)

    assert missing_result == empty_result


@pytest.mark.parametrize(
    ("filename", "contents"),
    [
        ("concepts.yml", "schema_version: [\n"),
        ("topics.yml", "schema_version: 1\n"),
        ("projects.yml", "schema_version: 2\nprojects: []\n"),
        ("claim-verification.json", '{"schema_version": 1, "reviews": [}\n'),
    ],
)
def test_present_corrupt_overlay_fails_closed(
    tmp_path: Path, filename: str, contents: str
) -> None:
    source = tmp_path / "source"
    shutil.copytree(FIXTURE, source)
    corpus = source / "corpus"
    corpus.mkdir()
    (corpus / filename).write_text(contents, encoding="utf-8")

    with pytest.raises((BuildError, RegistryValidationError)):
        build_site(source, tmp_path / "site", generated_at=None)


def test_dangling_claim_reference_names_review_and_occurrence(tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(FIXTURE, source)
    corpus = source / "corpus"
    corpus.mkdir()
    (corpus / "claim-verification.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reviews": [
                    {
                        "id": "review:dangling",
                        "claim_refs": ["claim:missing:key_claims:0"],
                        "claim_fingerprint": claim_fingerprint("missing"),
                        "status": "verified",
                        "method": "test",
                        "evidence": [{"url": "https://example.test", "note": "test"}],
                        "reviewed_at": "2026-09-11T00:00:00Z",
                        "reviewer": "test",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(BuildError, match="review:dangling.*claim:missing:key_claims:0"):
        build_site(source, tmp_path / "site", generated_at=None)


def test_source_manifest_is_unchanged_by_populated_build(tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(FIXTURE, source)
    _write_overlays(source, _populated_documents())
    before = _manifest(source)

    build_site(source, tmp_path / "site", generated_at=None)

    assert _manifest(source) == before
