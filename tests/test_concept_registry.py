from __future__ import annotations

from pathlib import Path

import pytest

from yt_insights_web.corpus.normalized_models import LabelOrigin, RawLabel
from yt_insights_web.corpus.registries import (
    MATCH_ALIAS,
    MATCH_SENTINEL,
    MATCH_UNRESOLVED,
    RegistryValidationError,
    concept_url,
    legacy_concept_url,
    load_registries,
    normalize_label,
    resolve_concept,
    resolve_project,
    resolve_source_record,
    resolve_topic,
)
from yt_insights_web.corpus.source_models import RawVideo
from yt_insights_web.load import load_corpus
from yt_insights_web.slug import concept_id as legacy_concept_id

ROOT = Path(__file__).resolve().parents[1]
V1_FIXTURE = ROOT / "tests" / "fixtures" / "v1-edge"


def _documents(
    *,
    concepts: list[dict[str, object]] | None = None,
    topics: list[dict[str, object]] | None = None,
    projects: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "concepts": {"schema_version": 1, "concepts": concepts or []},
        "topics": {"schema_version": 1, "topics": topics or []},
        "projects": {"schema_version": 1, "projects": projects or []},
        "claims": {"schema_version": 1, "reviews": []},
    }


def _entry(
    entry_id: str,
    canonical_name: str,
    aliases: list[str] | None = None,
    *,
    entry_type: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "id": entry_id,
        "canonical_name": canonical_name,
        "aliases": aliases or [],
        "status": "canonical",
    }
    if entry_type is not None:
        result["type"] = entry_type
    return result


def test_empty_registries_are_valid(tmp_path: Path) -> None:
    missing = load_registries(overlay_root=tmp_path)
    assert missing.concepts == ()
    assert missing.topics == ()
    assert missing.projects == ()
    assert missing.claim_reviews == ()

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "concepts.yml").write_text(
        "schema_version: 1\nconcepts: []\n", encoding="utf-8"
    )
    (corpus / "topics.yml").write_text("schema_version: 1\ntopics: []\n", encoding="utf-8")
    (corpus / "projects.yml").write_text(
        "schema_version: 1\nprojects: []\n", encoding="utf-8"
    )
    (corpus / "claim-verification.json").write_text(
        '{"schema_version": 1, "reviews": []}\n', encoding="utf-8"
    )

    empty = load_registries(overlay_root=tmp_path)
    assert empty == missing


def test_v1_label_resolves_through_alias() -> None:
    registries = load_registries(
        documents=_documents(
            concepts=[
                _entry(
                    "concept_context_engineering",
                    "context engineering",
                    ["context-engineering"],
                    entry_type="technical_concept",
                )
            ]
        )
    )
    raw = "  CONTEXT-engineering\t"
    resolved = resolve_concept(
        registries,
        RawLabel(
            label=raw,
            origin=LabelOrigin.INSIGHTS_EXTRACTION,
            location="insights.json::tags[2]",
        ),
    )

    assert resolved.raw_label == raw
    assert resolved.location == "insights.json::tags[2]"
    assert resolved.origin is LabelOrigin.INSIGHTS_EXTRACTION
    assert resolved.match_method == MATCH_ALIAS
    assert resolved.canonical_id == "concept_context_engineering"
    assert resolved.resolved_id == "concept_context_engineering"
    assert resolved.url == "concepts/concept_context_engineering/index.html"


def test_source_resolution_preserves_every_label_origin_and_endpoint() -> None:
    source = load_corpus(V1_FIXTURE).videos[0]
    assert isinstance(source, RawVideo)
    all_labels = (
        *source.frontmatter.tags,
        *source.insights.tags,
        source.insights.connections[0].concept,
        source.insights.connections[0].connects_to,
    )
    unique_labels = tuple(dict.fromkeys(normalize_label(label) for label in all_labels))
    registries = load_registries(
        documents=_documents(
            concepts=[
                _entry(f"concept_{index}", label)
                for index, label in enumerate(unique_labels)
            ]
        )
    )

    resolved = resolve_source_record(registries, source)

    assert len(resolved.labels) == len(source.frontmatter.tags) + len(source.insights.tags)
    assert [label.raw_label for label in resolved.labels] == [
        *source.frontmatter.tags,
        *source.insights.tags,
    ]
    assert [label.location for label in resolved.labels] == [
        *(f"artifacts/v1-edge-video/summary.md::frontmatter.tags[{index}]" for index in range(4)),
        *(f"artifacts/v1-edge-video/insights.json::tags[{index}]" for index in range(4)),
    ]
    assert [label.origin for label in resolved.labels] == [
        *(LabelOrigin.SUMMARY_FRONTMATTER for _ in source.frontmatter.tags),
        *(LabelOrigin.INSIGHTS_EXTRACTION for _ in source.insights.tags),
    ]

    connection = resolved.connections[0]
    assert connection.concept.raw_label == "多言語システム"
    assert (
        connection.concept.location
        == "artifacts/v1-edge-video/insights.json::connections[0].concept"
    )
    assert connection.connects_to.raw_label == "🧭 exploration"
    assert (
        connection.connects_to.location
        == "artifacts/v1-edge-video/insights.json::connections[0].connects_to"
    )
    assert connection.concept.origin is LabelOrigin.CONNECTION_ENDPOINT
    assert connection.connects_to.origin is LabelOrigin.CONNECTION_ENDPOINT
    assert resolved.project_fits[0].raw_fit == "new"
    assert resolved.project_fits[0].project_ref is None
    assert (
        resolved.project_fits[0].location
        == "artifacts/v1-edge-video/insights.json::project_ideas[0].fits"
    )


def test_unknown_label_becomes_deterministic_unresolved_node() -> None:
    registries = load_registries(documents=_documents())
    raw = "  Never\tRegistered  "
    source = RawLabel(raw, LabelOrigin.INSIGHTS_EXTRACTION, "insights.json::tags[2]")

    first = resolve_concept(registries, source)
    second = resolve_concept(registries, source)

    assert first == second
    assert first.raw_label == raw
    assert first.resolved_id is None
    assert first.match_method == MATCH_UNRESOLVED
    assert first.id == legacy_concept_id(raw)
    assert first.url == legacy_concept_url(raw)


def test_duplicate_normalized_alias_fails() -> None:
    documents = _documents(
        concepts=[
            _entry("concept_a", "First", ["Alpha\t Beta"]),
            _entry("concept_b", "Second", [" alpha beta "]),
        ]
    )
    with pytest.raises(RegistryValidationError) as first_error:
        load_registries(documents=documents)
    with pytest.raises(RegistryValidationError) as second_error:
        load_registries(
            documents=_documents(
                concepts=[
                    _entry("concept_b", "Second", [" alpha beta "]),
                    _entry("concept_a", "First", ["Alpha\t Beta"]),
                ]
            )
        )

    message = str(first_error.value)
    assert "concept_a" in message
    assert "concept_b" in message
    assert "concepts.yml::concepts[0]" in message
    assert "concepts.yml::concepts[1]" in message
    assert first_error.value.errors == tuple(sorted(first_error.value.errors))
    assert second_error.value.errors == tuple(sorted(second_error.value.errors))


def test_canonical_name_participates_in_alias_conflicts() -> None:
    with pytest.raises(RegistryValidationError, match="concept_a.*concept_b|concept_b.*concept_a"):
        load_registries(
            documents=_documents(
                concepts=[
                    _entry("concept_a", "Canonical label"),
                    _entry("concept_b", "Other", [" canonical\tLABEL "]),
                ]
            )
        )


def test_alias_normalization_is_nfc_casefold_whitespace_only() -> None:
    assert normalize_label("e\u0301 \t  Label") == normalize_label("\u00e9 label")
    assert normalize_label("\u00a0Mixed\u2003Whitespace\n") == "mixed whitespace"
    assert normalize_label("hyphen-label") != normalize_label("hyphen label")
    assert normalize_label("punctuation,") != normalize_label("punctuation")


def test_concept_rename_preserves_id_and_url() -> None:
    old = load_registries(
        documents=_documents(concepts=[_entry("concept_stable", "Old display name")])
    )
    renamed = load_registries(
        documents=_documents(concepts=[_entry("concept_stable", "New display name")])
    )

    before = resolve_concept(old, "Old display name")
    after = resolve_concept(renamed, "New display name")

    assert before.id == after.id == "concept_stable"
    assert before.url == after.url == concept_url("concept_stable")


def test_topic_alias_resolution() -> None:
    registries = load_registries(
        documents=_documents(
            topics=[_entry("topic_coding_agents", "coding agents", ["coding-agents"])]
        )
    )
    result = resolve_topic(registries, "CODING-agents")

    assert result.resolved_id == "topic_coding_agents"
    assert result.canonical_name == "coding agents"
    assert result.match_method == MATCH_ALIAS
    assert result.url == "topics/topic_coding_agents/index.html"


def test_project_aliases_and_unknown_values() -> None:
    registries = load_registries(
        documents=_documents(
            projects=[
                _entry("project_movement_lab", "Movement Lab", ["movement-lab"]),
                _entry("project_gatehouse", "Gatehouse", ["gatehouse"]),
                _entry("project_beyond_evals", "Beyond Evals", ["beyond-evals"]),
            ]
        )
    )

    assert resolve_project(registries, "movement-lab").project_ref == "project_movement_lab"
    assert resolve_project(registries, "gatehouse").project_ref == "project_gatehouse"
    assert resolve_project(registries, "beyond-evals").project_ref == "project_beyond_evals"
    unknown = resolve_project(registries, "future-project")
    assert unknown.raw_fit == "future-project"
    assert unknown.project_ref is None
    assert unknown.match_method == MATCH_UNRESOLVED


def test_new_project_fit_remains_unresolved_sentinel() -> None:
    registries = load_registries(
        documents=_documents(
            projects=[_entry("project_movement_lab", "Movement Lab", ["movement-lab"])]
        )
    )

    result = resolve_project(
        registries,
        "new",
        location="insights.json::project_ideas[0].fits",
    )

    assert result.raw_fit == "new"
    assert result.project_ref is None
    assert result.match_method == MATCH_SENTINEL
    assert result.location == "insights.json::project_ideas[0].fits"


def test_registry_entry_order_does_not_change_valid_or_invalid_results() -> None:
    first = _documents(
        concepts=[
            _entry("concept_b", "Beta", ["b"]),
            _entry("concept_a", "Alpha", ["a"]),
        ]
    )
    second = _documents(
        concepts=[
            _entry("concept_a", "Alpha", ["a"]),
            _entry("concept_b", "Beta", ["b"]),
        ]
    )

    first_entries = load_registries(documents=first).concepts
    second_entries = load_registries(documents=second).concepts
    assert [
        (entry.id, entry.canonical_name, entry.aliases, entry.status)
        for entry in first_entries
    ] == [
        (entry.id, entry.canonical_name, entry.aliases, entry.status)
        for entry in second_entries
    ]

    conflict_first = _documents(
        concepts=[
            _entry("concept_b", "Beta", ["same"]),
            _entry("concept_a", "Alpha", ["same"]),
        ]
    )
    conflict_second = _documents(
        concepts=[
            _entry("concept_a", "Alpha", ["same"]),
            _entry("concept_b", "Beta", ["same"]),
        ]
    )
    with pytest.raises(RegistryValidationError) as first_error:
        load_registries(documents=conflict_first)
    with pytest.raises(RegistryValidationError) as second_error:
        load_registries(documents=conflict_second)
    assert first_error.value.errors == tuple(sorted(first_error.value.errors))
    assert second_error.value.errors == tuple(sorted(second_error.value.errors))
    assert any("normalized label 'same'" in error for error in first_error.value.errors)
    assert any("normalized label 'same'" in error for error in second_error.value.errors)


def test_overlay_root_is_not_the_generated_site_tree(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    generated = tmp_path / "site" / "corpus"
    corpus.mkdir(parents=True)
    generated.mkdir(parents=True)
    (corpus / "concepts.yml").write_text(
        "schema_version: 1\nconcepts:\n"
        "  - id: concept_source\n"
        "    canonical_name: Source\n"
        "    aliases: []\n"
        "    status: canonical\n",
        encoding="utf-8",
    )
    (generated / "concepts.yml").write_text(
        "schema_version: 1\nconcepts:\n"
        "  - id: concept_site\n"
        "    canonical_name: Site\n"
        "    aliases: []\n"
        "    status: canonical\n",
        encoding="utf-8",
    )

    loaded = load_registries(overlay_root=tmp_path)

    assert [entry.id for entry in loaded.concepts] == ["concept_source"]
