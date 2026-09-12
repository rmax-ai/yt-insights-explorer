from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import UTC, datetime
from typing import get_type_hints

import pytest

from yt_insights_web.corpus.normalized_models import (
    Evidence,
    EvidenceAvailability,
    Fingerprint,
    FingerprintKind,
    IdentityKind,
    ItemIdentity,
    LabelOrigin,
    NormalizedItem,
    Provenance,
    RawLabel,
)
from yt_insights_web.corpus.source_models import (
    ArticleIdea,
    Connection,
    CoreInsight,
    DeepDive,
    EvidenceQuote,
    IndexItem,
    KeyClaim,
    LoadedCorpus,
    OpenQuestion,
    ProjectIdea,
    RawVideo,
    SourceFrontmatter,
    SourceInsights,
    SourceModelError,
    TradeoffAndFailureMode,
    V1SourceRecord,
    V2SourceRecord,
)
from yt_insights_web.models import (
    IndexItem as CompatibilityIndexItem,
)
from yt_insights_web.models import (
    LoadedCorpus as CompatibilityLoadedCorpus,
)
from yt_insights_web.models import (
    RawVideo as CompatibilityRawVideo,
)

TIMESTAMP = datetime(2026, 9, 12, tzinfo=UTC)


def _index() -> IndexItem:
    return IndexItem(
        video_id="typed-video",
        title="Typed video",
        channel="Typed channel",
        status="analyzed",
        ingested_at=TIMESTAMP,
        artifact_summary="artifacts/summary.md",
        artifact_insights="artifacts/insights.json",
        cost_usd_total=None,
    )


def _frontmatter() -> SourceFrontmatter:
    return SourceFrontmatter(
        type="Digest",
        title="Typed video",
        description="A typed source record.",
        id="urn:test:typed-video",
        status="complete",
        tags=("source-label",),
        confidence="high",
        visibility="private",
        source_type="youtube",
        source_uri="https://www.youtube.com/watch?v=typed-video",
        source_title="Typed video",
        source_author="Typed channel",
        source_published=TIMESTAMP,
        captured_at=TIMESTAMP,
        generated_by="test",
        review_status="unreviewed",
    )


def _insights_payload(*, schema_version: int | None) -> dict[str, object]:
    payload: dict[str, object] = {
        "core_insights": [
            {
                "insight": "A core insight.",
                "type": "architecture",
                "why_it_matters": "It matters.",
                "generalization": "It generalizes.",
                "evidence_quotes": ["A plain evidence quote."],
                "evidence_strength": "strong",
                "novelty": "high",
            }
        ],
        "deep_dives": [
            {
                "topic": "A topic",
                "research_question": "A question?",
                "why": "A reason.",
                "trigger_insight": "A trigger.",
                "evidence_quotes": [
                    {
                        "text": "A structured evidence quote.",
                        "timestamp_seconds": 12.5,
                        "source_url": "https://www.youtube.com/watch?v=typed-video",
                    }
                ],
                "priority": "medium",
            }
        ],
        "article_ideas": [
            {
                "title": "An article",
                "thesis": "A thesis.",
                "angle": "An angle.",
                "based_on": "The insight.",
                "audience": "Engineers.",
            }
        ],
        "project_ideas": [
            {
                "name": "A project",
                "hypothesis": "A hypothesis.",
                "poc": "A proof of concept.",
                "measurement": "A measurement.",
                "based_on": "The insight.",
                "fits": "new",
            }
        ],
        "architectural_implications": [
            {
                "observation": "An observation.",
                "before": "Before.",
                "after": "After.",
                "consequence": "A consequence.",
            }
        ],
        "tradeoffs_and_failure_modes": [
            {
                "topic": "A tradeoff",
                "benefit": "A benefit.",
                "cost_or_risk": "A risk.",
                "evidence_quote": "A tradeoff quote.",
            }
        ],
        "open_questions": [
            {
                "question": "An open question?",
                "why_unresolved": "It is unresolved.",
                "research_direction": "Research it.",
            }
        ],
        "key_claims": [
            {
                "claim": "A claim.",
                "claim_type": "factual",
                "evidence": "Claim evidence.",
                "verification_needed": True,
                "verification_question": "Can it be verified?",
            }
        ],
        "connections": [
            {
                "concept": "A concept",
                "connects_to": "Another concept",
                "relationship": "They connect.",
            }
        ],
        "tags": ["typed-label"],
    }
    if schema_version is not None:
        payload["schema_version"] = schema_version
    return payload


def _source_record_pair() -> tuple[V1SourceRecord, V2SourceRecord]:
    v1_insights = SourceInsights.from_mapping(_insights_payload(schema_version=None))
    v2_insights = SourceInsights.from_mapping(_insights_payload(schema_version=2))
    common = {
        "index": _index(),
        "frontmatter": _frontmatter(),
        "summary_markdown": "## Summary\n\nTyped source.",
        "summary_path": "artifacts/summary.md",
        "insights_path": "artifacts/insights.json",
    }
    return (
        V1SourceRecord(**common, insights=v1_insights),
        V2SourceRecord(**common, insights=v2_insights),
    )


def _provenance(*, source_index: int | None = None) -> Provenance:
    return Provenance(
        source_version=1,
        location="insights.json::core_insights[0]",
        source_index=source_index,
    )


def test_compatibility_imports_resolve_to_one_canonical_contract() -> None:
    assert CompatibilityRawVideo is RawVideo
    assert CompatibilityIndexItem is IndexItem
    assert CompatibilityLoadedCorpus is LoadedCorpus


def test_v1_and_v2_source_records_cover_all_sections_and_quote_forms() -> None:
    v1, v2 = _source_record_pair()

    assert v1.schema_version is None
    assert v2.schema_version == 2
    assert v1.source_version.value == 1
    assert v2.source_version.value == 2
    for record in (v1, v2):
        assert len(record.insights.core_insights) == 1
        assert len(record.insights.deep_dives) == 1
        assert len(record.insights.article_ideas) == 1
        assert len(record.insights.project_ideas) == 1
        assert len(record.insights.architectural_implications) == 1
        assert len(record.insights.tradeoffs_and_failure_modes) == 1
        assert len(record.insights.open_questions) == 1
        assert len(record.insights.key_claims) == 1
        assert len(record.insights.connections) == 1
        assert isinstance(record.insights.core_insights[0], CoreInsight)
        assert isinstance(record.insights.deep_dives[0], DeepDive)
        assert isinstance(record.insights.article_ideas[0], ArticleIdea)
        assert isinstance(record.insights.project_ideas[0], ProjectIdea)
        assert isinstance(record.insights.open_questions[0], OpenQuestion)
        assert isinstance(record.insights.key_claims[0], KeyClaim)
        assert isinstance(record.insights.connections[0], Connection)
        assert isinstance(record.insights.core_insights[0].evidence_quotes[0], EvidenceQuote)
        assert isinstance(record.insights.deep_dives[0].evidence_quotes[0], EvidenceQuote)
        assert record.insights.core_insights[0].evidence_quotes[0].timestamp_seconds is None
        assert record.insights.deep_dives[0].evidence_quotes[0].timestamp_seconds == 12.5
        assert isinstance(
            record.insights.tradeoffs_and_failure_modes[0],
            TradeoffAndFailureMode,
        )


def test_source_models_are_recursively_immutable() -> None:
    v1, _ = _source_record_pair()

    with pytest.raises(FrozenInstanceError):
        v1.summary_markdown = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        v1.insights.core_insights[0].text = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        v1.insights.core_insights[0].evidence_quotes[0].text = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        v1.insights.core_insights[0].evidence_quotes[0]["text"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        v1.frontmatter["tags"] = ("changed",)  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        v1.insights.tags += ("changed",)  # type: ignore[misc]


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda payload: payload.__setitem__("unknown", True),
            "unknown field",
        ),
        (
            lambda payload: payload["core_insights"][0].__setitem__("unknown", True),
            "unknown field",
        ),
        (
            lambda payload: payload["core_insights"][0]["evidence_quotes"].__setitem__(0, 12),
            "must be an object",
        ),
    ],
)
def test_source_models_reject_unknown_and_wrong_nested_values(mutator, message: str) -> None:
    payload = _insights_payload(schema_version=None)
    mutator(payload)

    with pytest.raises(SourceModelError, match=message):
        SourceInsights.from_mapping(payload)


def test_normalized_evidence_requires_explicit_availability_for_nullable_enrichment() -> None:
    provenance = _provenance()

    unavailable = Evidence(
        text="No timing was persisted.",
        availability=EvidenceAvailability.UNAVAILABLE,
        provenance=provenance,
    )
    unresolved = Evidence(
        text="A fuzzy candidate exists.",
        availability=EvidenceAvailability.UNRESOLVED,
        provenance=provenance,
    )
    candidate = Evidence(
        text="A proposed match exists.",
        availability=EvidenceAvailability.CANDIDATE,
        provenance=provenance,
        resolution_method="summary_quote_fuzzy",
    )
    resolved = Evidence(
        text="An exact match was accepted.",
        availability=EvidenceAvailability.RESOLVED_ENRICHMENT,
        provenance=provenance,
        timestamp_seconds=83,
        source_url="https://www.youtube.com/watch?v=typed-video&t=83s",
        resolution_method="summary_quote_exact",
    )

    assert unavailable.timestamp_seconds is None
    assert unavailable.source_url is None
    assert unresolved.timestamp_seconds is None
    assert unresolved.source_url is None
    assert candidate.timestamp_seconds is None
    assert candidate.source_url is None
    assert resolved.timestamp_seconds == 83

    with pytest.raises(ValueError, match="unavailable"):
        Evidence(
            text="No timing was persisted.",
            availability=EvidenceAvailability.UNAVAILABLE,
            provenance=provenance,
            timestamp_seconds=83,
        )
    with pytest.raises(ValueError, match="unresolved"):
        Evidence(
            text="A fuzzy candidate exists.",
            availability=EvidenceAvailability.UNRESOLVED,
            provenance=provenance,
            timestamp_seconds=83,
        )
    with pytest.raises(ValueError, match="candidate"):
        Evidence(
            text="A proposed match exists.",
            availability=EvidenceAvailability.CANDIDATE,
            provenance=provenance,
            source_url="https://example.test/fuzzy",
        )
    with pytest.raises(ValueError, match="resolved"):
        Evidence(
            text="No accepted enrichment.",
            availability=EvidenceAvailability.RESOLVED,
            provenance=provenance,
        )


def test_provenance_labels_identity_and_fingerprints_preserve_invariants() -> None:
    label = RawLabel(
        label="raw topic",
        origin=LabelOrigin.INSIGHTS_EXTRACTION,
        location="insights.json::tags[0]",
    )
    identity = ItemIdentity(
        id="typed-video:core_insights:0",
        kind=IdentityKind.LEGACY_POSITION,
        fingerprint=Fingerprint(
            "sha256:" + ("a" * 64),
            FingerprintKind.ITEM,
        ),
    )
    provenance = _provenance(source_index=0)
    item = NormalizedItem(identity=identity, provenance=provenance)

    assert label.raw_label == "raw topic"
    assert label.origin is LabelOrigin.INSIGHTS_EXTRACTION
    assert label.location.endswith("tags[0]")
    assert item.id_kind is IdentityKind.LEGACY_POSITION
    assert item.source_index == 0
    assert item.fingerprint.value.startswith("sha256:")

    with pytest.raises(ValueError, match="legacy-position"):
        NormalizedItem(
            identity=identity,
            provenance=_provenance(source_index=None),
        )
    with pytest.raises(ValueError, match="prefix"):
        Fingerprint("not-a-versioned-fingerprint", FingerprintKind.ITEM)


def test_normalized_contract_has_no_mutable_overlay_or_unrestricted_mapping_fields() -> None:
    from yt_insights_web.corpus import normalized_models

    for name, candidate in vars(normalized_models).items():
        if not is_dataclass(candidate):
            continue
        annotations = get_type_hints(candidate)
        rendered = repr(annotations)
        assert "dict" not in rendered.lower(), name
        assert "mapping" not in rendered.lower(), name
        assert "V1SourceRecord" not in rendered
        assert "V2SourceRecord" not in rendered
        assert all(field.default_factory is field.default_factory for field in fields(candidate))

    assert not hasattr(normalized_models, "Overlay")
    assert not hasattr(normalized_models, "Rendering")
