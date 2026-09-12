"""Version-neutral, immutable models produced by corpus compilation.

These classes intentionally describe corpus facts and provenance only.  They
do not model HTML, template overlays, or rendering dictionaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, IntEnum, StrEnum
from math import isfinite


class NormalizedModelError(ValueError):
    """Raised when a normalized record violates a contract invariant."""


def _error(path: str, message: str) -> NormalizedModelError:
    return NormalizedModelError(f"{path}: {message}")


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(path, "must be a non-empty string")
    return value


def _optional_text(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _text(value, path)


def _number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _error(path, "must be a number")
    result = float(value)
    if not isfinite(result) or result < 0:
        raise _error(path, "must be a finite, non-negative number")
    return result


def _optional_number(value: object, path: str) -> float | None:
    if value is None:
        return None
    return _number(value, path)


def _datetime(value: object, path: str) -> datetime:
    if not isinstance(value, datetime):
        raise _error(path, "must be a datetime instance")
    if value.tzinfo is None:
        raise _error(path, "datetime must include a timezone")
    return value


def _tuple(value: object, path: str) -> tuple[object, ...]:
    if not isinstance(value, (tuple, list)):
        raise _error(path, "must be a tuple or list")
    return tuple(value)


def _enum(value: object, enum_type: type[Enum], path: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        expected = ", ".join(member.value for member in enum_type)
        raise _error(path, f"unknown value {value!r}; expected one of {expected}") from exc


class EvidenceAvailability(StrEnum):
    """Whether evidence enrichment is absent, pending, proposed, or accepted."""

    UNAVAILABLE = "unavailable"
    UNRESOLVED = "unresolved"
    CANDIDATE = "candidate"
    RESOLVED = "resolved"
    RESOLVED_ENRICHMENT = "resolved-enrichment"


# Both names are useful at the boundary.  They intentionally identify one
# state machine rather than two competing interpretations of resolution.
EvidenceResolution = EvidenceAvailability
AvailabilityState = EvidenceAvailability
EvidenceStatus = EvidenceAvailability
ResolutionStatus = EvidenceAvailability


class IdentityKind(StrEnum):
    """The semantics of an item's stable identifier."""

    LEGACY_POSITION = "legacy-position"
    PERSISTED = "persisted"
    CONTENT = "content"
    DERIVED = "derived"


class FingerprintKind(StrEnum):
    """Algorithm families reserved for version-prefixed fingerprints."""

    ITEM = "item"
    EVIDENCE = "evidence"
    CLAIM = "claim"
    CONTENT = "content"


class InsightSection(StrEnum):
    """The nine source insight sections represented in the normalized model."""

    CORE_INSIGHTS = "core_insights"
    DEEP_DIVES = "deep_dives"
    ARTICLE_IDEAS = "article_ideas"
    PROJECT_IDEAS = "project_ideas"
    ARCHITECTURAL_IMPLICATIONS = "architectural_implications"
    TRADEOFFS_AND_FAILURE_MODES = "tradeoffs_and_failure_modes"
    OPEN_QUESTIONS = "open_questions"
    KEY_CLAIMS = "key_claims"
    CONNECTIONS = "connections"


class LabelOrigin(StrEnum):
    """Where a raw label was found in the source artifacts."""

    SUMMARY_FRONTMATTER = "summary_frontmatter"
    INSIGHTS_EXTRACTION = "insights_extraction"
    CONNECTION_ENDPOINT = "connection_endpoint"
    SOURCE_ARTIFACT = "source_artifact"


class SourceVersion(IntEnum):
    """Source schema version retained as provenance, not as a model branch."""

    V1 = 1
    V2 = 2


@dataclass(frozen=True, slots=True)
class Provenance:
    """Required source location and version for a normalized record."""

    source_version: SourceVersion | int
    location: str
    source_index: int | None = None

    def __post_init__(self) -> None:
        version = _enum(self.source_version, SourceVersion, "provenance.source_version")
        object.__setattr__(self, "source_version", version)
        object.__setattr__(self, "location", _text(self.location, "provenance.location"))
        if self.source_index is not None:
            if isinstance(self.source_index, bool) or not isinstance(self.source_index, int):
                raise _error("provenance.source_index", "must be a non-negative integer or null")
            if self.source_index < 0:
                raise _error("provenance.source_index", "must be non-negative when available")

    @property
    def version(self) -> int:
        """Return the numeric source version for serializers and adapters."""

        return int(self.source_version)


@dataclass(frozen=True, slots=True)
class RawLabel:
    """A source label retained verbatim with its origin and location."""

    label: str
    origin: LabelOrigin
    location: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", _text(self.label, "raw_label.label"))
        object.__setattr__(self, "origin", _enum(self.origin, LabelOrigin, "raw_label.origin"))
        object.__setattr__(self, "location", _text(self.location, "raw_label.location"))

    @property
    def raw_label(self) -> str:
        return self.label


@dataclass(frozen=True, slots=True)
class EvidenceOccurrence:
    """The source slot occupied by one evidence value."""

    section: str
    item_index: int
    quote_index: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "section", _text(self.section, "evidence_occurrence.section"))
        for name in ("item_index", "quote_index"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise _error(
                    f"evidence_occurrence.{name}",
                    "must be a non-negative integer",
                )

    @property
    def source_index(self) -> int:
        """Return the containing item's legacy source index."""

        return self.item_index

    @property
    def item_position(self) -> int:
        """Compatibility alias for the containing item's position."""

        return self.item_index


def _is_version_prefixed(value: str) -> bool:
    prefix, separator, body = value.partition(":")
    return bool(separator and prefix and body)


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """A full fingerprint whose algorithm/version prefix remains explicit."""

    value: str
    kind: FingerprintKind

    def __post_init__(self) -> None:
        value = _text(self.value, "fingerprint.value")
        if not _is_version_prefixed(value):
            raise _error(
                "fingerprint.value",
                "must retain a non-empty algorithm/version prefix and full value",
            )
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "kind", _enum(self.kind, FingerprintKind, "fingerprint.kind"))

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ItemIdentity:
    """An occurrence identity plus a separately versioned fingerprint."""

    id: str
    kind: IdentityKind
    fingerprint: Fingerprint | str
    fingerprint_kind: FingerprintKind = FingerprintKind.ITEM

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "identity.id"))
        identity_kind = _enum(self.kind, IdentityKind, "identity.kind")
        object.__setattr__(self, "kind", identity_kind)
        fingerprint_kind = _enum(
            self.fingerprint_kind, FingerprintKind, "identity.fingerprint_kind"
        )
        object.__setattr__(self, "fingerprint_kind", fingerprint_kind)
        fingerprint = self.fingerprint
        if isinstance(fingerprint, str):
            fingerprint = Fingerprint(fingerprint, fingerprint_kind)
        if not isinstance(fingerprint, Fingerprint):
            raise _error("identity.fingerprint", "must be a Fingerprint or version-prefixed string")
        if fingerprint.kind is not fingerprint_kind:
            raise _error("identity.fingerprint_kind", "does not agree with fingerprint.kind")
        object.__setattr__(self, "fingerprint", fingerprint)

    @property
    def id_kind(self) -> IdentityKind:
        return self.kind

    @property
    def fingerprint_value(self) -> str:
        return self.fingerprint.value


Identity = ItemIdentity


@dataclass(frozen=True, slots=True)
class Evidence:
    """Evidence with an explicit state for nullable enrichment."""

    text: str
    availability: EvidenceAvailability
    provenance: Provenance
    timestamp_seconds: float | None = None
    source_url: str | None = None
    resolution_method: str | None = None
    content_id: str | None = None
    occurrence: EvidenceOccurrence | None = None
    timestamp_method: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _text(self.text, "evidence.text"))
        availability = _enum(self.availability, EvidenceAvailability, "evidence.availability")
        object.__setattr__(self, "availability", availability)
        if not isinstance(self.provenance, Provenance):
            raise _error("evidence.provenance", "must be a Provenance")
        timestamp = _optional_number(self.timestamp_seconds, "evidence.timestamp_seconds")
        source_url = _optional_text(self.source_url, "evidence.source_url")
        method = _optional_text(self.resolution_method, "evidence.resolution_method")
        timestamp_method = _optional_text(self.timestamp_method, "evidence.timestamp_method")
        if method is not None and timestamp_method is not None and method != timestamp_method:
            raise _error(
                "evidence.timestamp_method",
                "must agree with resolution_method when both are provided",
            )
        method = timestamp_method or method
        content_id = _optional_text(self.content_id, "evidence.content_id")
        if self.occurrence is not None and not isinstance(self.occurrence, EvidenceOccurrence):
            raise _error("evidence.occurrence", "must be an EvidenceOccurrence or null")

        if availability is EvidenceAvailability.UNAVAILABLE:
            if timestamp is not None or source_url is not None or method is not None:
                raise _error(
                    "evidence",
                    "unavailable evidence cannot carry an accepted timestamp, source, or method",
                )
        elif availability in (
            EvidenceAvailability.UNRESOLVED,
            EvidenceAvailability.CANDIDATE,
        ):
            if timestamp is not None or source_url is not None:
                raise _error(
                    "evidence",
                    (
                        f"{availability.value} evidence must keep timestamp_seconds "
                        "and source_url null"
                    ),
                )
        elif availability in (
            EvidenceAvailability.RESOLVED,
            EvidenceAvailability.RESOLVED_ENRICHMENT,
        ):
            if timestamp is None and source_url is None:
                raise _error(
                    "evidence",
                    "resolved evidence requires a non-null timestamp_seconds or source_url",
                )

        object.__setattr__(self, "timestamp_seconds", timestamp)
        object.__setattr__(self, "source_url", source_url)
        object.__setattr__(self, "resolution_method", method)
        object.__setattr__(self, "timestamp_method", method)
        object.__setattr__(self, "content_id", content_id)

    @property
    def evidence_id(self) -> str | None:
        """Return the exact-text content identity when one was assigned."""

        return self.content_id

    @property
    def content_identity(self) -> str | None:
        """Compatibility alias for the exact-text content identity."""

        return self.content_id

    @property
    def id(self) -> str | None:
        """Compatibility alias for the evidence content identity."""

        return self.content_id

    @property
    def occurrence_ref(self) -> EvidenceOccurrence | None:
        """Return the source occurrence reference."""

        return self.occurrence

    @property
    def occurrence_reference(self) -> EvidenceOccurrence | None:
        """Compatibility alias for the source occurrence reference."""

        return self.occurrence


NormalizedEvidence = Evidence


@dataclass(frozen=True, slots=True)
class NormalizedItem:
    """Common identity and provenance carried by every normalized item."""

    identity: ItemIdentity
    provenance: Provenance

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ItemIdentity):
            raise _error("normalized_item.identity", "must be an ItemIdentity")
        if not isinstance(self.provenance, Provenance):
            raise _error("normalized_item.provenance", "must be a Provenance")
        if (
            self.identity.kind is IdentityKind.LEGACY_POSITION
            and self.provenance.source_index is None
        ):
            raise _error(
                "normalized_item",
                "legacy-position identity requires an available source_index",
            )

    @property
    def id(self) -> str:
        return self.identity.id

    @property
    def id_kind(self) -> IdentityKind:
        return self.identity.kind

    @property
    def source_index(self) -> int | None:
        return self.provenance.source_index

    @property
    def fingerprint(self) -> Fingerprint:
        return self.identity.fingerprint


@dataclass(frozen=True, slots=True)
class NormalizedSource:
    """Source metadata without source-version-specific branching."""

    source_type: str
    uri: str
    title: str
    author: str
    published_at: datetime

    def __post_init__(self) -> None:
        for name in ("source_type", "uri", "title", "author"):
            object.__setattr__(self, name, _text(getattr(self, name), f"normalized_source.{name}"))
        object.__setattr__(
            self, "published_at", _datetime(self.published_at, "normalized_source.published_at")
        )


@dataclass(frozen=True, slots=True)
class NormalizedDocument:
    """Document metadata preserved from source frontmatter."""

    type: str
    description: str
    urn: str
    status: str
    confidence: str
    visibility: str
    captured_at: datetime
    generated_by: str
    review_status: str

    def __post_init__(self) -> None:
        for name in (
            "type",
            "description",
            "urn",
            "status",
            "confidence",
            "visibility",
            "generated_by",
            "review_status",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), f"document.{name}"))
        object.__setattr__(self, "captured_at", _datetime(self.captured_at, "document.captured_at"))


@dataclass(frozen=True, slots=True)
class NormalizedSummary:
    """Plain summary content; HTML remains a renderer concern."""

    markdown: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "markdown", _text(self.markdown, "summary.markdown"))


@dataclass(frozen=True, slots=True)
class NormalizedIndexItem:
    """Normalized processing metadata for one indexed video."""

    video_id: str
    title: str
    channel: str
    status: str
    ingested_at: datetime
    cost_usd_total: float | None
    provenance: Provenance

    def __post_init__(self) -> None:
        for name in ("video_id", "title", "channel", "status"):
            object.__setattr__(self, name, _text(getattr(self, name), f"index_item.{name}"))
        object.__setattr__(
            self, "ingested_at", _datetime(self.ingested_at, "index_item.ingested_at")
        )
        object.__setattr__(
            self,
            "cost_usd_total",
            _optional_number(self.cost_usd_total, "index_item.cost_usd_total"),
        )
        if not isinstance(self.provenance, Provenance):
            raise _error("index_item.provenance", "must be a Provenance")


@dataclass(frozen=True, slots=True)
class NormalizedCoreInsight(NormalizedItem):
    insight: str
    type: str
    why_it_matters: str
    generalization: str
    evidence: tuple[Evidence, ...]
    evidence_strength: str
    novelty: str

    def __post_init__(self) -> None:
        NormalizedItem.__post_init__(self)
        for name in (
            "insight",
            "type",
            "why_it_matters",
            "generalization",
            "evidence_strength",
            "novelty",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), f"core_insight.{name}"))
        evidence = _tuple(self.evidence, "core_insight.evidence")
        if any(not isinstance(item, Evidence) for item in evidence):
            raise _error("core_insight.evidence", "must contain only Evidence values")
        object.__setattr__(self, "evidence", evidence)

    @property
    def evidence_quotes(self) -> tuple[Evidence, ...]:
        return self.evidence


@dataclass(frozen=True, slots=True)
class NormalizedDeepDive(NormalizedItem):
    topic: str
    research_question: str
    why: str
    trigger_insight: str
    evidence: tuple[Evidence, ...]
    priority: str

    def __post_init__(self) -> None:
        NormalizedItem.__post_init__(self)
        for name in ("topic", "research_question", "why", "trigger_insight", "priority"):
            object.__setattr__(self, name, _text(getattr(self, name), f"deep_dive.{name}"))
        evidence = _tuple(self.evidence, "deep_dive.evidence")
        if any(not isinstance(item, Evidence) for item in evidence):
            raise _error("deep_dive.evidence", "must contain only Evidence values")
        object.__setattr__(self, "evidence", evidence)

    @property
    def evidence_quotes(self) -> tuple[Evidence, ...]:
        return self.evidence


@dataclass(frozen=True, slots=True)
class NormalizedArticleIdea(NormalizedItem):
    title: str
    thesis: str
    angle: str
    based_on: str
    audience: str

    def __post_init__(self) -> None:
        NormalizedItem.__post_init__(self)
        for name in ("title", "thesis", "angle", "based_on", "audience"):
            object.__setattr__(self, name, _text(getattr(self, name), f"article_idea.{name}"))


@dataclass(frozen=True, slots=True)
class NormalizedProjectIdea(NormalizedItem):
    name: str
    hypothesis: str
    poc: str
    measurement: str
    based_on: str
    raw_fit: str

    def __post_init__(self) -> None:
        NormalizedItem.__post_init__(self)
        for name in ("name", "hypothesis", "poc", "measurement", "based_on", "raw_fit"):
            object.__setattr__(self, name, _text(getattr(self, name), f"project_idea.{name}"))

    @property
    def fits(self) -> str:
        return self.raw_fit


@dataclass(frozen=True, slots=True)
class NormalizedArchitecturalImplication(NormalizedItem):
    observation: str
    before: str
    after: str
    consequence: str

    def __post_init__(self) -> None:
        NormalizedItem.__post_init__(self)
        for name in ("observation", "before", "after", "consequence"):
            object.__setattr__(
                self, name, _text(getattr(self, name), f"architectural_implication.{name}")
            )


@dataclass(frozen=True, slots=True)
class NormalizedTradeoff(NormalizedItem):
    topic: str
    benefit: str
    cost_or_risk: str
    evidence: Evidence

    def __post_init__(self) -> None:
        NormalizedItem.__post_init__(self)
        for name in ("topic", "benefit", "cost_or_risk"):
            object.__setattr__(self, name, _text(getattr(self, name), f"tradeoff.{name}"))
        if not isinstance(self.evidence, Evidence):
            raise _error("tradeoff.evidence", "must be an Evidence")

    @property
    def evidence_quote(self) -> Evidence:
        return self.evidence


@dataclass(frozen=True, slots=True)
class NormalizedOpenQuestion(NormalizedItem):
    question: str
    why_unresolved: str
    research_direction: str

    def __post_init__(self) -> None:
        NormalizedItem.__post_init__(self)
        for name in ("question", "why_unresolved", "research_direction"):
            object.__setattr__(self, name, _text(getattr(self, name), f"open_question.{name}"))


@dataclass(frozen=True, slots=True)
class NormalizedKeyClaim(NormalizedItem):
    claim: str
    claim_type: str
    evidence: Evidence
    verification_requested: bool
    verification_question: str | None
    claim_fingerprint: Fingerprint | str | None = None

    def __post_init__(self) -> None:
        NormalizedItem.__post_init__(self)
        object.__setattr__(self, "claim", _text(self.claim, "key_claim.claim"))
        object.__setattr__(self, "claim_type", _text(self.claim_type, "key_claim.claim_type"))
        if not isinstance(self.evidence, Evidence):
            raise _error("key_claim.evidence", "must be an Evidence")
        if not isinstance(self.verification_requested, bool):
            raise _error("key_claim.verification_requested", "must be a boolean")
        object.__setattr__(
            self,
            "verification_question",
            _optional_text(self.verification_question, "key_claim.verification_question"),
        )
        fingerprint = self.claim_fingerprint
        if isinstance(fingerprint, str):
            fingerprint = Fingerprint(fingerprint, FingerprintKind.CLAIM)
        if fingerprint is not None:
            if not isinstance(fingerprint, Fingerprint):
                raise _error(
                    "key_claim.claim_fingerprint",
                    "must be a Fingerprint, version-prefixed string, or null",
                )
            if fingerprint.kind is not FingerprintKind.CLAIM:
                raise _error(
                    "key_claim.claim_fingerprint",
                    "must use the claim fingerprint kind",
                )
        object.__setattr__(self, "claim_fingerprint", fingerprint)

    @property
    def verification_needed(self) -> bool:
        return self.verification_requested

    @property
    def claim_fingerprint_value(self) -> str | None:
        """Return the lexical claim fingerprint as text."""

        return self.claim_fingerprint.value if self.claim_fingerprint is not None else None


@dataclass(frozen=True, slots=True)
class NormalizedConnection(NormalizedItem):
    concept: str
    connects_to: str
    relationship: str
    labels: tuple[RawLabel, ...]

    def __post_init__(self) -> None:
        NormalizedItem.__post_init__(self)
        for name in ("concept", "connects_to", "relationship"):
            object.__setattr__(self, name, _text(getattr(self, name), f"connection.{name}"))
        labels = _tuple(self.labels, "connection.labels")
        if any(not isinstance(item, RawLabel) for item in labels):
            raise _error("connection.labels", "must contain only RawLabel values")
        object.__setattr__(self, "labels", labels)


@dataclass(frozen=True, slots=True)
class NormalizedConcept:
    """A normalized concept name with no registry or overlay state."""

    name: str
    video_ids: tuple[str, ...]
    provenance: Provenance

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "concept.name"))
        video_ids = _tuple(self.video_ids, "concept.video_ids")
        if any(not isinstance(item, str) or not item for item in video_ids):
            raise _error("concept.video_ids", "must contain only non-empty strings")
        object.__setattr__(self, "video_ids", video_ids)
        if not isinstance(self.provenance, Provenance):
            raise _error("concept.provenance", "must be a Provenance")


@dataclass(frozen=True, slots=True)
class NormalizedVideo:
    """One version-neutral video with all nine typed insight sections."""

    identity: ItemIdentity
    provenance: Provenance
    video_id: str
    title: str
    channel: str
    status: str
    ingested_at: datetime
    source: NormalizedSource
    document: NormalizedDocument
    summary: NormalizedSummary
    labels: tuple[RawLabel, ...]
    core_insights: tuple[NormalizedCoreInsight, ...]
    deep_dives: tuple[NormalizedDeepDive, ...]
    article_ideas: tuple[NormalizedArticleIdea, ...]
    project_ideas: tuple[NormalizedProjectIdea, ...]
    architectural_implications: tuple[NormalizedArchitecturalImplication, ...]
    tradeoffs_and_failure_modes: tuple[NormalizedTradeoff, ...]
    open_questions: tuple[NormalizedOpenQuestion, ...]
    key_claims: tuple[NormalizedKeyClaim, ...]
    connections: tuple[NormalizedConnection, ...]

    def __post_init__(self) -> None:
        NormalizedItem.__post_init__(
            NormalizedItem(identity=self.identity, provenance=self.provenance)
        )
        for name in ("video_id", "title", "channel", "status"):
            object.__setattr__(self, name, _text(getattr(self, name), f"video.{name}"))
        object.__setattr__(self, "ingested_at", _datetime(self.ingested_at, "video.ingested_at"))
        for name, item_type in (
            ("source", NormalizedSource),
            ("document", NormalizedDocument),
            ("summary", NormalizedSummary),
        ):
            if not isinstance(getattr(self, name), item_type):
                raise _error(f"video.{name}", f"must be a {item_type.__name__}")
        labels = _tuple(self.labels, "video.labels")
        if any(not isinstance(item, RawLabel) for item in labels):
            raise _error("video.labels", "must contain only RawLabel values")
        object.__setattr__(self, "labels", labels)
        sections = (
            ("core_insights", NormalizedCoreInsight),
            ("deep_dives", NormalizedDeepDive),
            ("article_ideas", NormalizedArticleIdea),
            ("project_ideas", NormalizedProjectIdea),
            ("architectural_implications", NormalizedArchitecturalImplication),
            ("tradeoffs_and_failure_modes", NormalizedTradeoff),
            ("open_questions", NormalizedOpenQuestion),
            ("key_claims", NormalizedKeyClaim),
            ("connections", NormalizedConnection),
        )
        for name, item_type in sections:
            values = _tuple(getattr(self, name), f"video.{name}")
            if any(not isinstance(item, item_type) for item in values):
                raise _error(f"video.{name}", f"must contain only {item_type.__name__} values")
            object.__setattr__(self, name, values)

    @property
    def id(self) -> str:
        return self.identity.id


@dataclass(frozen=True, slots=True)
class NormalizedCorpus:
    """The immutable compiler output before rendering/export projection."""

    videos: tuple[NormalizedVideo, ...]
    concepts: tuple[NormalizedConcept, ...]
    index_items: tuple[NormalizedIndexItem, ...]
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        for name, item_type in (
            ("videos", NormalizedVideo),
            ("concepts", NormalizedConcept),
            ("index_items", NormalizedIndexItem),
        ):
            values = _tuple(getattr(self, name), f"corpus.{name}")
            if any(not isinstance(item, item_type) for item in values):
                raise _error(f"corpus.{name}", f"must contain only {item_type.__name__} values")
            object.__setattr__(self, name, values)
        warnings = _tuple(self.warnings, "corpus.warnings")
        if any(not isinstance(item, str) for item in warnings):
            raise _error("corpus.warnings", "must contain only strings")
        object.__setattr__(self, "warnings", warnings)


SourceProvenance = Provenance
NormalizedLabel = RawLabel


__all__ = [
    "AvailabilityState",
    "Evidence",
    "EvidenceAvailability",
    "EvidenceOccurrence",
    "EvidenceResolution",
    "EvidenceStatus",
    "Fingerprint",
    "FingerprintKind",
    "Identity",
    "IdentityKind",
    "InsightSection",
    "ItemIdentity",
    "LabelOrigin",
    "NormalizedArchitecturalImplication",
    "NormalizedArticleIdea",
    "NormalizedConnection",
    "NormalizedConcept",
    "NormalizedCorpus",
    "NormalizedCoreInsight",
    "NormalizedDeepDive",
    "NormalizedDocument",
    "NormalizedEvidence",
    "NormalizedIndexItem",
    "NormalizedItem",
    "NormalizedKeyClaim",
    "NormalizedOpenQuestion",
    "NormalizedProjectIdea",
    "NormalizedSource",
    "NormalizedSummary",
    "NormalizedTradeoff",
    "NormalizedVideo",
    "NormalizedModelError",
    "NormalizedLabel",
    "Provenance",
    "RawLabel",
    "ResolutionStatus",
    "SourceVersion",
    "SourceProvenance",
]
