from __future__ import annotations

import json
from pathlib import Path

import pytest

from yt_insights_web.corpus.registries import RegistryValidationError, load_registries


def _documents() -> dict[str, object]:
    return {
        "concepts": {"schema_version": 1, "concepts": []},
        "topics": {"schema_version": 1, "topics": []},
        "projects": {"schema_version": 1, "projects": []},
        "claims": {"schema_version": 1, "reviews": []},
    }


def _load_with_concepts(document: object):
    documents = _documents()
    documents["concepts"] = document
    return load_registries(documents=documents)


def test_missing_files_are_empty_but_present_empty_files_validate(tmp_path: Path) -> None:
    assert load_registries(overlay_root=tmp_path).concepts == ()

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "concepts.yml").write_text("schema_version: 1\nconcepts: []\n", encoding="utf-8")

    assert load_registries(overlay_root=tmp_path).concepts == ()


def test_malformed_yaml_reports_overlay_path(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "concepts.yml").write_text("schema_version: [\n", encoding="utf-8")

    with pytest.raises(RegistryValidationError, match=r"corpus/concepts\.yml.*malformed"):
        load_registries(overlay_root=tmp_path)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ([], "envelope must be an object"),
        ({"concepts": []}, "missing required field 'schema_version'"),
        ({"schema_version": 2, "concepts": []}, "unsupported version 2"),
        ({"schema_version": 1, "concepts": {}}, "concepts: must be a list"),
    ],
)
def test_malformed_envelopes_fail_actionably(document: object, message: str) -> None:
    with pytest.raises(RegistryValidationError, match=message):
        _load_with_concepts(document)


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (
            {
                "id": "bad id",
                "canonical_name": "Name",
                "aliases": [],
                "status": "canonical",
            },
            "must match lowercase stable-ID syntax",
        ),
        (
            {
                "id": "concept_missing_name",
                "aliases": [],
                "status": "canonical",
            },
            "canonical_name: must be a non-empty string",
        ),
        (
            {
                "id": "concept_bad_aliases",
                "canonical_name": "Name",
                "aliases": "not-a-list",
                "status": "canonical",
            },
            "aliases: must be a list of strings",
        ),
        (
            {
                "id": "concept_bad_type",
                "canonical_name": "Name",
                "aliases": [],
                "status": "canonical",
                "type": 7,
            },
            "type: must be a non-empty string",
        ),
        (42, "entry must be an object"),
    ],
)
def test_invalid_entries_include_entry_index_and_reason(entry: object, message: str) -> None:
    with pytest.raises(RegistryValidationError) as error:
        _load_with_concepts({"schema_version": 1, "concepts": [entry]})

    rendered = str(error.value)
    assert "concepts.yml::concepts[0]" in rendered
    assert message in rendered


def test_duplicate_ids_name_both_locations() -> None:
    entries = [
        {
            "id": "concept_duplicate",
            "canonical_name": "First",
            "aliases": [],
            "status": "canonical",
        },
        {
            "id": "concept_duplicate",
            "canonical_name": "Second",
            "aliases": [],
            "status": "retired",
        },
    ]

    with pytest.raises(RegistryValidationError) as error:
        _load_with_concepts({"schema_version": 1, "concepts": entries})

    rendered = str(error.value)
    assert "duplicate ID conflict" in rendered
    assert "concepts.yml::concepts[0]" in rendered
    assert "concepts.yml::concepts[1]" in rendered


def test_present_claim_document_requires_the_versioned_reviews_envelope(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "claim-verification.json").write_text(
        json.dumps({"reviews": []}), encoding="utf-8"
    )

    with pytest.raises(RegistryValidationError, match=r"claim-verification\.json.*schema_version"):
        load_registries(overlay_root=tmp_path)


def test_invalid_claim_review_entry_is_actionable() -> None:
    documents = _documents()
    documents["claims"] = {
        "schema_version": 1,
        "reviews": [{"id": "review_one", "claim_refs": "claim:not-a-list"}],
    }

    with pytest.raises(RegistryValidationError) as error:
        load_registries(documents=documents)

    rendered = str(error.value)
    assert "claim-verification.json::reviews[0]" in rendered
    assert "missing required field" in rendered
    assert "claim_refs: must be a list" in rendered
