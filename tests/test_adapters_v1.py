from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from yt_insights_web.corpus.adapters.v1 import adapt_v1
from yt_insights_web.corpus.compiler import compile_v1
from yt_insights_web.corpus.evidence import (
    SUMMARY_QUOTE_TIMESTAMP_METHOD,
    parse_markdown_quote_line,
    youtube_source_url,
)
from yt_insights_web.corpus.identity import (
    claim_fingerprint,
    evidence_content_id,
    item_fingerprint,
)
from yt_insights_web.corpus.normalized_models import (
    EvidenceAvailability,
    NormalizedCorpus,
)
from yt_insights_web.corpus.source_models import (
    CoreInsight,
    DeepDive,
    EvidenceQuote,
    IndexItem,
    KeyClaim,
    SourceFrontmatter,
    SourceInsights,
    TradeoffAndFailureMode,
    V1SourceRecord,
)

TIMESTAMP = datetime(2026, 9, 12, tzinfo=UTC)
VIDEO_ID = "adapter-video"
SOURCE_URI = f"https://www.youtube.com/watch?v={VIDEO_ID}"


def _source(
    *,
    summary_markdown: str = "Summary body",
    core_quotes: tuple[str | EvidenceQuote, ...] = ("Core evidence",),
    deep_quotes: tuple[str | EvidenceQuote, ...] = ("Deep evidence",),
    tradeoff_quote: str | EvidenceQuote = "Tradeoff evidence",
    claim_evidence: str = "Claim evidence",
    source_uri: str = SOURCE_URI,
) -> V1SourceRecord:
    insights = SourceInsights(
        core_insights=(
            CoreInsight(
                insight="Core insight",
                type="architecture",
                why_it_matters="It matters",
                generalization="It generalizes",
                evidence_quotes=core_quotes,
                evidence_strength="strong",
                novelty="high",
            ),
        ),
        deep_dives=(
            DeepDive(
                topic="Deep dive",
                research_question="What is the question?",
                why="It matters",
                trigger_insight="Core insight",
                evidence_quotes=deep_quotes,
                priority="medium",
            ),
        ),
        article_ideas=(),
        project_ideas=(),
        architectural_implications=(),
        tradeoffs_and_failure_modes=(
            TradeoffAndFailureMode(
                topic="Tradeoff",
                benefit="A benefit",
                cost_or_risk="A risk",
                evidence_quote=tradeoff_quote,
            ),
        ),
        open_questions=(),
        key_claims=(
            KeyClaim(
                claim="A key claim",
                claim_type="factual",
                evidence=claim_evidence,
                verification_needed=True,
                verification_question=None,
            ),
        ),
        connections=(),
        tags=(),
    )
    return V1SourceRecord(
        index=IndexItem(
            video_id=VIDEO_ID,
            title="Adapter video",
            channel="Adapter channel",
            status="analyzed",
            ingested_at=TIMESTAMP,
            artifact_summary="artifacts/adapter/summary.md",
            artifact_insights="artifacts/adapter/insights.json",
            cost_usd_total=None,
        ),
        frontmatter=SourceFrontmatter(
            type="Digest",
            title="Adapter video",
            description="A source record",
            id=f"urn:test:{VIDEO_ID}",
            status="complete",
            tags=("frontmatter-tag",),
            confidence="high",
            visibility="private",
            source_type="youtube",
            source_uri=source_uri,
            source_title="Adapter video",
            source_author="Adapter channel",
            source_published=TIMESTAMP,
            captured_at=TIMESTAMP,
            generated_by="test",
            review_status="unreviewed",
        ),
        summary_markdown=summary_markdown,
        insights=insights,
        summary_path="artifacts/adapter/summary.md",
        insights_path="artifacts/adapter/insights.json",
    )


def test_preserves_legacy_occurrence_ids() -> None:
    video = adapt_v1(_source())

    for section in (
        "core_insights",
        "deep_dives",
        "tradeoffs_and_failure_modes",
        "key_claims",
    ):
        item = getattr(video, section)[0]
        assert item.id == f"{VIDEO_ID}:{section}:0"
        assert item.id_kind == "legacy-position"
        assert item.source_index == 0
        assert item.fingerprint.value.startswith("item-fingerprint-v1:")
        assert len(item.fingerprint.value.rsplit(":", 1)[1]) == 64


def test_fingerprint_is_versioned_and_deterministic() -> None:
    expected_payload = b"item-fingerprint-v1\x00video\x00core_insights\x000\x00primary"
    expected = "item-fingerprint-v1:" + hashlib.sha256(expected_payload).hexdigest()

    assert item_fingerprint("video", "core_insights", 0, "primary") == expected
    assert item_fingerprint("video", "core_insights", 0, "primary") == expected
    assert item_fingerprint("video", "core_insights", None, "primary") != expected

    claim = claim_fingerprint("  Same\tCLAIM  ")
    assert claim == claim_fingerprint("same claim")
    assert claim.startswith("claim-fingerprint-v1:")
    assert len(claim.rsplit(":", 1)[1]) == 64


def test_inline_evidence_uses_exact_text_identity() -> None:
    values = ("Exact text", "exact text", "Exact text.", "Exact  text")
    ids = [evidence_content_id(VIDEO_ID, value) for value in values]

    assert all(value.startswith(f"q:{VIDEO_ID}:") for value in ids)
    assert all(len(value.rsplit(":", 1)[1]) == 64 for value in ids)
    assert len(set(ids)) == len(values)
    assert ids[0] == evidence_content_id(VIDEO_ID, "Exact text")


def test_unique_exact_summary_quote_recovers_timestamp() -> None:
    text = "An exact summary quote"
    source = _source(
        summary_markdown=f'> "{text}" (at 2:03)\n',
        core_quotes=(text,),
    )
    evidence = adapt_v1(source).core_insights[0].evidence[0]

    assert evidence.text == text
    assert evidence.timestamp_seconds == 123
    assert evidence.timestamp_method == SUMMARY_QUOTE_TIMESTAMP_METHOD
    assert evidence.resolution_method == SUMMARY_QUOTE_TIMESTAMP_METHOD
    assert evidence.availability is EvidenceAvailability.RESOLVED_ENRICHMENT
    assert evidence.source_url == f"{SOURCE_URI}&t=123s"


def test_ambiguous_or_fuzzy_match_keeps_timestamp_null() -> None:
    exact = "Repeated exact quote"
    fuzzy = "Only a substring"
    summary = (
        f'> "{exact}" (at 1:00)\n'
        f'> "{exact}" (at 2:00)\n'
        f'> "{fuzzy} with more words" (at 3:00)\n'
    )
    video = adapt_v1(
        _source(
            summary_markdown=summary,
            core_quotes=(exact,),
            deep_quotes=(fuzzy,),
        )
    )

    ambiguous = video.core_insights[0].evidence[0]
    substring = video.deep_dives[0].evidence[0]
    assert ambiguous.timestamp_seconds is None
    assert ambiguous.timestamp_method is None
    assert substring.timestamp_seconds is None
    assert substring.timestamp_method is None


def test_missing_optional_values_degrade_without_failure() -> None:
    video = adapt_v1(
        _source(
            summary_markdown="No matching quote",
            core_quotes=(EvidenceQuote(text="No timestamp", timestamp_seconds=None),),
            source_uri="https://www.youtube.com/watch?v=adapter-video",
        )
    )

    evidence = video.core_insights[0].evidence[0]
    assert evidence.timestamp_seconds is None
    assert evidence.timestamp_method is None
    assert evidence.source_url == SOURCE_URI
    assert video.key_claims[0].verification_question is None
    assert video.key_claims[0].claim_fingerprint_value is not None


def test_duplicate_evidence_keeps_content_identity_but_has_occurrences() -> None:
    text = "The same evidence twice"
    video = adapt_v1(
        _source(
            core_quotes=(text, EvidenceQuote(text=text)),
            summary_markdown=f'> "{text}" (at 0:42)\n> "{text}" (at 1:42)\n',
        )
    )
    evidence = video.core_insights[0].evidence

    assert evidence[0].content_id == evidence[1].content_id
    assert evidence[0].occurrence_ref != evidence[1].occurrence_ref
    assert evidence[0].occurrence_ref.section == "core_insights"
    assert evidence[0].occurrence_ref.item_index == 0
    assert evidence[0].occurrence_ref.quote_index == 0
    assert evidence[1].occurrence_ref.quote_index == 1
    assert evidence[0].timestamp_seconds is None
    assert evidence[1].timestamp_seconds is None


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("core_insights", "evidence"),
        ("deep_dives", "evidence"),
        ("tradeoffs_and_failure_modes", "evidence"),
        ("key_claims", "evidence"),
    ],
)
def test_every_v1_evidence_field_is_adapted(section: str, field: str) -> None:
    video = adapt_v1(_source())
    item = getattr(video, section)[0]
    evidence = getattr(item, field)
    if not isinstance(evidence, tuple):
        evidence = (evidence,)
    assert evidence
    assert all(item.content_id is not None for item in evidence)
    assert all(item.occurrence_ref is not None for item in evidence)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('> "A quote" (at 12:34)', ("A quote", 754)),
        ('> "A quote"', ("A quote", None)),
        ('> "A quote" (at 12:3)', None),
        (' > "A quote" (at 12:34)', None),
        ('> A quote (at 12:34)', None),
        ('> "A quote" (12:34)', None),
    ],
)
def test_markdown_quote_parser_contract(line: str, expected: tuple[str, int | None] | None) -> None:
    assert parse_markdown_quote_line(line) == expected


def test_url_seconds_encoding() -> None:
    assert youtube_source_url(SOURCE_URI, VIDEO_ID, 83) == f"{SOURCE_URI}&t=83s"
    assert youtube_source_url(SOURCE_URI, VIDEO_ID, 12.5) == f"{SOURCE_URI}&t=12.5s"
    assert youtube_source_url(SOURCE_URI, VIDEO_ID) == SOURCE_URI


def test_claim_evidence_is_wrapped_and_claim_fingerprint_is_separate() -> None:
    video = adapt_v1(_source(claim_evidence="Claim evidence"))
    claim = video.key_claims[0]

    assert claim.evidence.text == "Claim evidence"
    assert claim.evidence.occurrence_ref.section == "key_claims"
    assert claim.claim_fingerprint_value == claim_fingerprint("A key claim")
    assert claim.evidence.content_id != claim.claim_fingerprint_value


def test_compiler_is_pure(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("compiler attempted external I/O")

    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)

    result = compile_v1((_source(),))

    assert isinstance(result, NormalizedCorpus)
    assert len(result.videos) == 1
    assert result.videos[0].id == VIDEO_ID
