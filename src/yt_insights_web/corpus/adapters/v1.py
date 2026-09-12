"""Adapt typed V1 source records into the version-neutral corpus model."""

from __future__ import annotations

from ..evidence import evidence_from_quote
from ..identity import claim_fingerprint, item_fingerprint, legacy_occurrence_id
from ..normalized_models import (
    Evidence,
    FingerprintKind,
    IdentityKind,
    ItemIdentity,
    LabelOrigin,
    NormalizedArchitecturalImplication,
    NormalizedArticleIdea,
    NormalizedConnection,
    NormalizedCoreInsight,
    NormalizedDeepDive,
    NormalizedDocument,
    NormalizedKeyClaim,
    NormalizedOpenQuestion,
    NormalizedProjectIdea,
    NormalizedSource,
    NormalizedSummary,
    NormalizedTradeoff,
    NormalizedVideo,
    Provenance,
    RawLabel,
    SourceVersion,
)
from ..source_models import (
    ArchitecturalImplication,
    ArticleIdea,
    Connection,
    CoreInsight,
    DeepDive,
    KeyClaim,
    OpenQuestion,
    ProjectIdea,
    RawVideo,
    TradeoffAndFailureMode,
    V1SourceRecord,
)


def _item_identity(video_id: str, section: str, index: int, primary_text: str) -> ItemIdentity:
    return ItemIdentity(
        id=legacy_occurrence_id(video_id, section, index),
        kind=IdentityKind.LEGACY_POSITION,
        fingerprint=item_fingerprint(video_id, section, index, primary_text),
        fingerprint_kind=FingerprintKind.ITEM,
    )


def _provenance(source: RawVideo, section: str, index: int) -> Provenance:
    return Provenance(
        source_version=SourceVersion.V1,
        location=f"{source.insights_path}::{section}[{index}]",
        source_index=index,
    )


def _evidence(
    source: V1SourceRecord,
    quote: object,
    *,
    section: str,
    item_index: int,
    quote_index: int,
) -> Evidence:
    return evidence_from_quote(
        quote,
        video_id=source.video_id,
        section=section,
        item_index=item_index,
        quote_index=quote_index,
        summary_markdown=source.summary_markdown,
        source_uri=source.frontmatter.source_uri,
        source_version=int(SourceVersion.V1),
        location=f"{source.insights_path}::{section}[{item_index}]",
    )


def _evidence_list(
    source: V1SourceRecord,
    quotes: tuple[object, ...],
    *,
    section: str,
    item_index: int,
) -> tuple[Evidence, ...]:
    return tuple(
        _evidence(
            source,
            quote,
            section=section,
            item_index=item_index,
            quote_index=quote_index,
        )
        for quote_index, quote in enumerate(quotes)
    )


def _labels(source: V1SourceRecord) -> tuple[RawLabel, ...]:
    frontmatter_labels = tuple(
        RawLabel(
            label=label,
            origin=LabelOrigin.SUMMARY_FRONTMATTER,
            location=f"{source.summary_path}::frontmatter.tags[{index}]",
        )
        for index, label in enumerate(source.frontmatter.tags)
    )
    insight_labels = tuple(
        RawLabel(
            label=label,
            origin=LabelOrigin.INSIGHTS_EXTRACTION,
            location=f"{source.insights_path}::tags[{index}]",
        )
        for index, label in enumerate(source.insights.tags)
    )
    return frontmatter_labels + insight_labels


def _video_identity(source: V1SourceRecord) -> ItemIdentity:
    return ItemIdentity(
        id=source.video_id,
        kind=IdentityKind.PERSISTED,
        fingerprint=item_fingerprint(source.video_id, "video", None, source.video_id),
        fingerprint_kind=FingerprintKind.ITEM,
    )


def _adapt_core_insight(
    source: V1SourceRecord, index: int, item: CoreInsight
) -> NormalizedCoreInsight:
    return NormalizedCoreInsight(
        identity=_item_identity(source.video_id, "core_insights", index, item.insight),
        provenance=_provenance(source, "core_insights", index),
        insight=item.insight,
        type=item.type,
        why_it_matters=item.why_it_matters,
        generalization=item.generalization,
        evidence=_evidence_list(
            source,
            item.evidence_quotes,
            section="core_insights",
            item_index=index,
        ),
        evidence_strength=item.evidence_strength,
        novelty=item.novelty,
    )


def _adapt_deep_dive(source: V1SourceRecord, index: int, item: DeepDive) -> NormalizedDeepDive:
    return NormalizedDeepDive(
        identity=_item_identity(source.video_id, "deep_dives", index, item.topic),
        provenance=_provenance(source, "deep_dives", index),
        topic=item.topic,
        research_question=item.research_question,
        why=item.why,
        trigger_insight=item.trigger_insight,
        evidence=_evidence_list(
            source,
            item.evidence_quotes,
            section="deep_dives",
            item_index=index,
        ),
        priority=item.priority,
    )


def _adapt_article_idea(
    source: V1SourceRecord, index: int, item: ArticleIdea
) -> NormalizedArticleIdea:
    return NormalizedArticleIdea(
        identity=_item_identity(source.video_id, "article_ideas", index, item.title),
        provenance=_provenance(source, "article_ideas", index),
        title=item.title,
        thesis=item.thesis,
        angle=item.angle,
        based_on=item.based_on,
        audience=item.audience,
    )


def _adapt_project_idea(
    source: V1SourceRecord, index: int, item: ProjectIdea
) -> NormalizedProjectIdea:
    return NormalizedProjectIdea(
        identity=_item_identity(source.video_id, "project_ideas", index, item.name),
        provenance=_provenance(source, "project_ideas", index),
        name=item.name,
        hypothesis=item.hypothesis,
        poc=item.poc,
        measurement=item.measurement,
        based_on=item.based_on,
        raw_fit=item.fits,
    )


def _adapt_architectural_implication(
    source: V1SourceRecord, index: int, item: ArchitecturalImplication
) -> NormalizedArchitecturalImplication:
    return NormalizedArchitecturalImplication(
        identity=_item_identity(
            source.video_id,
            "architectural_implications",
            index,
            item.observation,
        ),
        provenance=_provenance(source, "architectural_implications", index),
        observation=item.observation,
        before=item.before,
        after=item.after,
        consequence=item.consequence,
    )


def _adapt_tradeoff(
    source: V1SourceRecord, index: int, item: TradeoffAndFailureMode
) -> NormalizedTradeoff:
    return NormalizedTradeoff(
        identity=_item_identity(
            source.video_id,
            "tradeoffs_and_failure_modes",
            index,
            item.topic,
        ),
        provenance=_provenance(source, "tradeoffs_and_failure_modes", index),
        topic=item.topic,
        benefit=item.benefit,
        cost_or_risk=item.cost_or_risk,
        evidence=_evidence(
            source,
            item.evidence_quote,
            section="tradeoffs_and_failure_modes",
            item_index=index,
            quote_index=0,
        ),
    )


def _adapt_open_question(
    source: V1SourceRecord, index: int, item: OpenQuestion
) -> NormalizedOpenQuestion:
    return NormalizedOpenQuestion(
        identity=_item_identity(source.video_id, "open_questions", index, item.question),
        provenance=_provenance(source, "open_questions", index),
        question=item.question,
        why_unresolved=item.why_unresolved,
        research_direction=item.research_direction,
    )


def _adapt_key_claim(source: V1SourceRecord, index: int, item: KeyClaim) -> NormalizedKeyClaim:
    return NormalizedKeyClaim(
        identity=_item_identity(source.video_id, "key_claims", index, item.claim),
        provenance=_provenance(source, "key_claims", index),
        claim=item.claim,
        claim_type=item.claim_type,
        evidence=_evidence(
            source,
            item.evidence,
            section="key_claims",
            item_index=index,
            quote_index=0,
        ),
        verification_requested=item.verification_needed,
        verification_question=item.verification_question,
        claim_fingerprint=claim_fingerprint(item.claim),
    )


def _adapt_connection(
    source: V1SourceRecord, index: int, item: Connection
) -> NormalizedConnection:
    return NormalizedConnection(
        identity=_item_identity(source.video_id, "connections", index, item.concept),
        provenance=_provenance(source, "connections", index),
        concept=item.concept,
        connects_to=item.connects_to,
        relationship=item.relationship,
        labels=(),
    )


def adapt_v1(source: V1SourceRecord | RawVideo) -> NormalizedVideo:
    """Adapt one immutable V1 source record without side effects.

    Every section occurrence keeps its published positional ID and source
    index.  A full versioned item fingerprint is carried separately.  Inline
    evidence is wrapped in the shared normalized ``Evidence`` type, including
    a content ID and an explicit section/item/quote occurrence reference.
    """

    if not isinstance(source, RawVideo) or source.source_version.value != int(SourceVersion.V1):
        raise TypeError("adapt_v1 expects a V1SourceRecord or an implicit-V1 RawVideo")

    frontmatter = source.frontmatter
    return NormalizedVideo(
        identity=_video_identity(source),
        provenance=Provenance(
            source_version=SourceVersion.V1,
            location=source.summary_path,
        ),
        video_id=source.video_id,
        title=source.title,
        channel=source.channel,
        status=source.index.status,
        ingested_at=source.index.ingested_at,
        source=NormalizedSource(
            source_type=frontmatter.source_type,
            uri=frontmatter.source_uri,
            title=frontmatter.source_title,
            author=frontmatter.source_author,
            published_at=frontmatter.source_published,
        ),
        document=NormalizedDocument(
            type=frontmatter.type,
            description=frontmatter.description,
            urn=frontmatter.id,
            status=frontmatter.status,
            confidence=frontmatter.confidence,
            visibility=frontmatter.visibility,
            captured_at=frontmatter.captured_at,
            generated_by=frontmatter.generated_by,
            review_status=frontmatter.review_status,
        ),
        summary=NormalizedSummary(markdown=source.summary_markdown),
        labels=_labels(source),
        core_insights=tuple(
            _adapt_core_insight(source, index, item)
            for index, item in enumerate(source.insights.core_insights)
        ),
        deep_dives=tuple(
            _adapt_deep_dive(source, index, item)
            for index, item in enumerate(source.insights.deep_dives)
        ),
        article_ideas=tuple(
            _adapt_article_idea(source, index, item)
            for index, item in enumerate(source.insights.article_ideas)
        ),
        project_ideas=tuple(
            _adapt_project_idea(source, index, item)
            for index, item in enumerate(source.insights.project_ideas)
        ),
        architectural_implications=tuple(
            _adapt_architectural_implication(source, index, item)
            for index, item in enumerate(source.insights.architectural_implications)
        ),
        tradeoffs_and_failure_modes=tuple(
            _adapt_tradeoff(source, index, item)
            for index, item in enumerate(source.insights.tradeoffs_and_failure_modes)
        ),
        open_questions=tuple(
            _adapt_open_question(source, index, item)
            for index, item in enumerate(source.insights.open_questions)
        ),
        key_claims=tuple(
            _adapt_key_claim(source, index, item)
            for index, item in enumerate(source.insights.key_claims)
        ),
        connections=tuple(
            _adapt_connection(source, index, item)
            for index, item in enumerate(source.insights.connections)
        ),
    )


adapt = adapt_v1
adapt_v1_record = adapt_v1
adapt_source = adapt_v1


__all__ = ["adapt", "adapt_source", "adapt_v1", "adapt_v1_record"]
