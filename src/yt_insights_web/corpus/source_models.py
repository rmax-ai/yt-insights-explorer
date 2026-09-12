"""Immutable, typed models for source corpus records.

The loader still owns file-system access and first-pass validation.  These
models are the lossless, typed boundary after that validation.  Mapping
factories are intentionally strict so a mutable parser result cannot leak
into the compiler boundary.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from math import isfinite
from pathlib import Path
from typing import ClassVar

INSIGHT_SECTIONS = (
    "core_insights",
    "deep_dives",
    "article_ideas",
    "project_ideas",
    "architectural_implications",
    "tradeoffs_and_failure_modes",
    "open_questions",
    "key_claims",
    "connections",
)
INSIGHT_TYPES = (
    "architecture",
    "mechanism",
    "mental_model",
    "practice",
    "empirical_result",
    "failure_mode",
    "prediction",
    "tradeoff",
)
EVIDENCE_STRENGTHS = ("weak", "moderate", "strong")
NOVELTIES = ("low", "medium", "high")
PRIORITIES = ("low", "medium", "high")
CLAIM_TYPES = ("causal", "comparative", "factual", "opinion", "prediction")
PROJECT_FITS = ("beyond-evals", "gatehouse", "movement-lab", "new")
INDEX_STATUSES = ("analyzed", "skipped", "failed")
ARCHITECTURAL_IMPLICATION_FIELDS = ("observation", "before", "after", "consequence")


class SourceModelError(ValueError):
    """Raised when a source model cannot be constructed safely."""


SourceValidationError = SourceModelError


class SourceVersion(IntEnum):
    """Artifact schema versions at the typed source boundary."""

    V1 = 1
    V2 = 2
    IMPLICIT_V1 = 1
    EXPLICIT_V2 = 2


def _error(path: str, message: str) -> SourceModelError:
    return SourceModelError(f"{path}: {message}")


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(path, "must be a non-empty string")
    return value


def _optional_text(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _text(value, path)


def _enum_text(value: object, allowed: tuple[str, ...], path: str) -> str:
    if not isinstance(value, str):
        raise _error(path, "must be a string enum")
    if value not in allowed:
        raise _error(path, f"unknown enum {value!r}; expected one of {', '.join(allowed)}")
    return value


def _strict_mapping(
    value: object,
    path: str,
    *,
    allowed: tuple[str, ...],
    required: tuple[str, ...],
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _error(path, "must be an object")
    keys = tuple(value)
    unknown = sorted(
        (key for key in keys if not isinstance(key, str) or key not in allowed),
        key=repr,
    )
    if unknown:
        rendered = ", ".join(repr(key) for key in unknown)
        raise _error(path, f"unknown field(s): {rendered}")
    missing = [key for key in required if key not in value]
    if missing:
        rendered = ", ".join(repr(key) for key in missing)
        raise _error(path, f"missing required field(s): {rendered}")
    return value


def _required(mapping: Mapping[str, object], key: str, path: str) -> object:
    return mapping[key]


def _tuple(value: object, path: str) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise _error(path, "must be an immutable tuple")
    return value


def _parsed_sequence(value: object, path: str) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise _error(path, "must be a list or tuple")
    return tuple(value)


def _strings(value: object, path: str) -> tuple[str, ...]:
    return tuple(
        _text(item, f"{path}[{index}]") for index, item in enumerate(_parsed_sequence(value, path))
    )


def _datetime(value: object, path: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise _error(path, f"invalid ISO datetime {value!r}") from exc
    if not isinstance(value, datetime):
        raise _error(path, "must be an ISO datetime or datetime instance")
    if value.tzinfo is None:
        raise _error(path, "datetime must include a timezone")
    return value


def _number_or_none(value: object, path: str) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _error(path, "must be a number or null")
    if isinstance(value, float) and not isfinite(value):
        raise _error(path, "must be finite")
    if value < 0:
        raise _error(path, "must not be negative")
    return value


def _schema_version(value: object, path: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error(path, "must be schema version 2 when present")
    if value != 2:
        raise _error(path, "must be explicit schema version 2 when present")
    return value


class _RecordMapping(Mapping[str, object]):
    """Read-only mapping compatibility for the pre-typed normalizer."""

    __slots__ = ()
    _fields: ClassVar[tuple[str, ...]]

    def __getitem__(self, key: str) -> object:
        if key not in self._mapping_fields():
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._mapping_fields())

    def __len__(self) -> int:
        return len(self._mapping_fields())

    def _mapping_fields(self) -> tuple[str, ...]:
        return self._fields


@dataclass(frozen=True, slots=True)
class _FrozenMapping(Mapping[str, object]):
    """Immutable compatibility view for records the loader already rejected."""

    entries: tuple[tuple[str, object], ...]

    def __getitem__(self, key: str) -> object:
        for entry_key, value in self.entries:
            if entry_key == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self.entries)

    def __len__(self) -> int:
        return len(self.entries)


def _freeze(value: object) -> object:
    """Recursively freeze a parser value without retaining mutable containers."""

    if isinstance(value, Mapping):
        return _FrozenMapping(tuple((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class IndexItem:
    """One validated entry from the source index."""

    video_id: str
    title: str
    channel: str
    status: str
    ingested_at: datetime
    artifact_summary: str | None
    artifact_insights: str | None
    cost_usd_total: float | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "video_id", _text(self.video_id, "index.video_id"))
        object.__setattr__(self, "title", _text(self.title, "index.title"))
        object.__setattr__(self, "channel", _text(self.channel, "index.channel"))
        object.__setattr__(self, "status", _enum_text(self.status, INDEX_STATUSES, "index.status"))
        object.__setattr__(self, "ingested_at", _datetime(self.ingested_at, "index.ingested_at"))
        object.__setattr__(
            self,
            "artifact_summary",
            _optional_text(self.artifact_summary, "index.artifact_summary"),
        )
        object.__setattr__(
            self,
            "artifact_insights",
            _optional_text(self.artifact_insights, "index.artifact_insights"),
        )
        cost = _number_or_none(self.cost_usd_total, "index.cost_usd_total")
        object.__setattr__(self, "cost_usd_total", float(cost) if cost is not None else None)


@dataclass(frozen=True, slots=True)
class EvidenceQuote(_RecordMapping):
    """A V1 inline quote, whether originally a string or an object."""

    text: str
    timestamp_seconds: int | float | None = None
    source_url: str | None = None

    _fields: ClassVar[tuple[str, ...]] = ("text", "timestamp_seconds", "source_url")

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _text(self.text, "evidence_quote.text"))
        timestamp = _number_or_none(self.timestamp_seconds, "evidence_quote.timestamp_seconds")
        object.__setattr__(self, "timestamp_seconds", timestamp)
        object.__setattr__(
            self,
            "source_url",
            _optional_text(self.source_url, "evidence_quote.source_url"),
        )

    @classmethod
    def from_value(cls, value: object, path: str) -> EvidenceQuote:
        if isinstance(value, str):
            return cls(text=_text(value, path))
        mapping = _strict_mapping(
            value,
            path,
            allowed=("text", "timestamp_seconds", "source_url"),
            required=("text",),
        )
        return cls(
            text=_text(_required(mapping, "text", path), f"{path}.text"),
            timestamp_seconds=_number_or_none(
                mapping.get("timestamp_seconds"), f"{path}.timestamp_seconds"
            ),
            source_url=_optional_text(mapping.get("source_url"), f"{path}.source_url"),
        )


Quote = EvidenceQuote


def _quotes(value: object, path: str) -> tuple[EvidenceQuote, ...]:
    values = _parsed_sequence(value, path)
    return tuple(
        item
        if isinstance(item, EvidenceQuote)
        else EvidenceQuote.from_value(item, f"{path}[{index}]")
        for index, item in enumerate(values)
    )


@dataclass(frozen=True, slots=True)
class SourceFrontmatter(_RecordMapping):
    """Typed OKF metadata from a summary frontmatter block."""

    type: str
    title: str
    description: str
    id: str
    status: str
    tags: tuple[str, ...]
    confidence: str
    visibility: str
    source_type: str
    source_uri: str
    source_title: str
    source_author: str
    source_published: datetime
    captured_at: datetime
    generated_by: str
    review_status: str

    _fields: ClassVar[tuple[str, ...]] = (
        "type",
        "title",
        "description",
        "id",
        "status",
        "tags",
        "confidence",
        "visibility",
        "source_type",
        "source_uri",
        "source_title",
        "source_author",
        "source_published",
        "captured_at",
        "generated_by",
        "review_status",
    )

    def __post_init__(self) -> None:
        for name in (
            "type",
            "title",
            "description",
            "id",
            "status",
            "confidence",
            "visibility",
            "source_type",
            "source_uri",
            "source_title",
            "source_author",
            "generated_by",
            "review_status",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), f"frontmatter.{name}"))
        object.__setattr__(
            self,
            "tags",
            _strings(_tuple(self.tags, "frontmatter.tags"), "frontmatter.tags"),
        )
        object.__setattr__(
            self,
            "source_published",
            _datetime(self.source_published, "frontmatter.source_published"),
        )
        object.__setattr__(
            self,
            "captured_at",
            _datetime(self.captured_at, "frontmatter.captured_at"),
        )

    @classmethod
    def from_mapping(cls, value: object, path: str = "frontmatter") -> SourceFrontmatter:
        fields = cls._fields
        mapping = _strict_mapping(value, path, allowed=fields, required=fields)
        return cls(
            type=_text(_required(mapping, "type", path), f"{path}.type"),
            title=_text(_required(mapping, "title", path), f"{path}.title"),
            description=_text(_required(mapping, "description", path), f"{path}.description"),
            id=_text(_required(mapping, "id", path), f"{path}.id"),
            status=_text(_required(mapping, "status", path), f"{path}.status"),
            tags=_strings(_required(mapping, "tags", path), f"{path}.tags"),
            confidence=_text(_required(mapping, "confidence", path), f"{path}.confidence"),
            visibility=_text(_required(mapping, "visibility", path), f"{path}.visibility"),
            source_type=_text(_required(mapping, "source_type", path), f"{path}.source_type"),
            source_uri=_text(_required(mapping, "source_uri", path), f"{path}.source_uri"),
            source_title=_text(_required(mapping, "source_title", path), f"{path}.source_title"),
            source_author=_text(_required(mapping, "source_author", path), f"{path}.source_author"),
            source_published=_datetime(
                _required(mapping, "source_published", path), f"{path}.source_published"
            ),
            captured_at=_datetime(_required(mapping, "captured_at", path), f"{path}.captured_at"),
            generated_by=_text(_required(mapping, "generated_by", path), f"{path}.generated_by"),
            review_status=_text(_required(mapping, "review_status", path), f"{path}.review_status"),
        )


@dataclass(frozen=True, slots=True)
class CoreInsight(_RecordMapping):
    insight: str
    type: str
    why_it_matters: str
    generalization: str
    evidence_quotes: tuple[EvidenceQuote, ...]
    evidence_strength: str
    novelty: str

    _fields: ClassVar[tuple[str, ...]] = (
        "insight",
        "type",
        "why_it_matters",
        "generalization",
        "evidence_quotes",
        "evidence_strength",
        "novelty",
    )

    def __post_init__(self) -> None:
        for name in ("insight", "why_it_matters", "generalization"):
            object.__setattr__(self, name, _text(getattr(self, name), f"core_insight.{name}"))
        object.__setattr__(self, "type", _enum_text(self.type, INSIGHT_TYPES, "core_insight.type"))
        object.__setattr__(
            self,
            "evidence_quotes",
            _quotes(
                _tuple(self.evidence_quotes, "core_insight.evidence_quotes"),
                "core_insight.evidence_quotes",
            ),
        )
        object.__setattr__(
            self,
            "evidence_strength",
            _enum_text(
                self.evidence_strength, EVIDENCE_STRENGTHS, "core_insight.evidence_strength"
            ),
        )
        object.__setattr__(
            self,
            "novelty",
            _enum_text(self.novelty, NOVELTIES, "core_insight.novelty"),
        )

    @classmethod
    def from_mapping(cls, value: object, index: int) -> CoreInsight:
        path = f"insights.json::core_insights[{index}]"
        mapping = _strict_mapping(value, path, allowed=cls._fields, required=cls._fields)
        return cls(
            insight=_text(mapping["insight"], f"{path}.insight"),
            type=_enum_text(mapping["type"], INSIGHT_TYPES, f"{path}.type"),
            why_it_matters=_text(mapping["why_it_matters"], f"{path}.why_it_matters"),
            generalization=_text(mapping["generalization"], f"{path}.generalization"),
            evidence_quotes=_quotes(mapping["evidence_quotes"], f"{path}.evidence_quotes"),
            evidence_strength=_enum_text(
                mapping["evidence_strength"], EVIDENCE_STRENGTHS, f"{path}.evidence_strength"
            ),
            novelty=_enum_text(mapping["novelty"], NOVELTIES, f"{path}.novelty"),
        )


@dataclass(frozen=True, slots=True)
class DeepDive(_RecordMapping):
    topic: str
    research_question: str
    why: str
    trigger_insight: str
    evidence_quotes: tuple[EvidenceQuote, ...]
    priority: str

    _fields: ClassVar[tuple[str, ...]] = (
        "topic",
        "research_question",
        "why",
        "trigger_insight",
        "evidence_quotes",
        "priority",
    )

    def __post_init__(self) -> None:
        for name in ("topic", "research_question", "why", "trigger_insight"):
            object.__setattr__(self, name, _text(getattr(self, name), f"deep_dive.{name}"))
        object.__setattr__(
            self,
            "evidence_quotes",
            _quotes(
                _tuple(self.evidence_quotes, "deep_dive.evidence_quotes"),
                "deep_dive.evidence_quotes",
            ),
        )
        object.__setattr__(
            self,
            "priority",
            _enum_text(self.priority, PRIORITIES, "deep_dive.priority"),
        )

    @classmethod
    def from_mapping(cls, value: object, index: int) -> DeepDive:
        path = f"insights.json::deep_dives[{index}]"
        mapping = _strict_mapping(value, path, allowed=cls._fields, required=cls._fields)
        return cls(
            topic=_text(mapping["topic"], f"{path}.topic"),
            research_question=_text(mapping["research_question"], f"{path}.research_question"),
            why=_text(mapping["why"], f"{path}.why"),
            trigger_insight=_text(mapping["trigger_insight"], f"{path}.trigger_insight"),
            evidence_quotes=_quotes(mapping["evidence_quotes"], f"{path}.evidence_quotes"),
            priority=_enum_text(mapping["priority"], PRIORITIES, f"{path}.priority"),
        )


@dataclass(frozen=True, slots=True)
class ArticleIdea(_RecordMapping):
    title: str
    thesis: str
    angle: str
    based_on: str
    audience: str

    _fields: ClassVar[tuple[str, ...]] = ("title", "thesis", "angle", "based_on", "audience")

    def __post_init__(self) -> None:
        for name in self._fields:
            object.__setattr__(self, name, _text(getattr(self, name), f"article_idea.{name}"))

    @classmethod
    def from_mapping(cls, value: object, index: int) -> ArticleIdea:
        path = f"insights.json::article_ideas[{index}]"
        mapping = _strict_mapping(value, path, allowed=cls._fields, required=cls._fields)
        return cls(**{name: _text(mapping[name], f"{path}.{name}") for name in cls._fields})


@dataclass(frozen=True, slots=True)
class ProjectIdea(_RecordMapping):
    name: str
    hypothesis: str
    poc: str
    measurement: str
    based_on: str
    fits: str

    _fields: ClassVar[tuple[str, ...]] = (
        "name",
        "hypothesis",
        "poc",
        "measurement",
        "based_on",
        "fits",
    )

    def __post_init__(self) -> None:
        for name in ("name", "hypothesis", "poc", "measurement", "based_on"):
            object.__setattr__(self, name, _text(getattr(self, name), f"project_idea.{name}"))
        object.__setattr__(self, "fits", _enum_text(self.fits, PROJECT_FITS, "project_idea.fits"))

    @classmethod
    def from_mapping(cls, value: object, index: int) -> ProjectIdea:
        path = f"insights.json::project_ideas[{index}]"
        mapping = _strict_mapping(value, path, allowed=cls._fields, required=cls._fields)
        return cls(
            name=_text(mapping["name"], f"{path}.name"),
            hypothesis=_text(mapping["hypothesis"], f"{path}.hypothesis"),
            poc=_text(mapping["poc"], f"{path}.poc"),
            measurement=_text(mapping["measurement"], f"{path}.measurement"),
            based_on=_text(mapping["based_on"], f"{path}.based_on"),
            fits=_enum_text(mapping["fits"], PROJECT_FITS, f"{path}.fits"),
        )


@dataclass(frozen=True, slots=True)
class ArchitecturalImplication(_RecordMapping):
    observation: str
    before: str
    after: str
    consequence: str

    _fields: ClassVar[tuple[str, ...]] = ARCHITECTURAL_IMPLICATION_FIELDS

    def __post_init__(self) -> None:
        for name in self._fields:
            object.__setattr__(
                self, name, _text(getattr(self, name), f"architectural_implication.{name}")
            )

    @classmethod
    def from_mapping(cls, value: object, index: int) -> ArchitecturalImplication:
        path = f"insights.json::architectural_implications[{index}]"
        mapping = _strict_mapping(value, path, allowed=cls._fields, required=cls._fields)
        return cls(**{name: _text(mapping[name], f"{path}.{name}") for name in cls._fields})


@dataclass(frozen=True, slots=True)
class TradeoffAndFailureMode(_RecordMapping):
    topic: str
    benefit: str
    cost_or_risk: str
    evidence_quote: EvidenceQuote

    _fields: ClassVar[tuple[str, ...]] = ("topic", "benefit", "cost_or_risk", "evidence_quote")

    def __post_init__(self) -> None:
        for name in ("topic", "benefit", "cost_or_risk"):
            object.__setattr__(self, name, _text(getattr(self, name), f"tradeoff.{name}"))
        value = self.evidence_quote
        if not isinstance(value, EvidenceQuote):
            value = EvidenceQuote.from_value(value, "tradeoff.evidence_quote")
        object.__setattr__(self, "evidence_quote", value)

    @classmethod
    def from_mapping(cls, value: object, index: int) -> TradeoffAndFailureMode:
        path = f"insights.json::tradeoffs_and_failure_modes[{index}]"
        mapping = _strict_mapping(value, path, allowed=cls._fields, required=cls._fields)
        return cls(
            topic=_text(mapping["topic"], f"{path}.topic"),
            benefit=_text(mapping["benefit"], f"{path}.benefit"),
            cost_or_risk=_text(mapping["cost_or_risk"], f"{path}.cost_or_risk"),
            evidence_quote=EvidenceQuote.from_value(
                mapping["evidence_quote"], f"{path}.evidence_quote"
            ),
        )


@dataclass(frozen=True, slots=True)
class OpenQuestion(_RecordMapping):
    question: str
    why_unresolved: str
    research_direction: str

    _fields: ClassVar[tuple[str, ...]] = ("question", "why_unresolved", "research_direction")

    def __post_init__(self) -> None:
        for name in self._fields:
            object.__setattr__(self, name, _text(getattr(self, name), f"open_question.{name}"))

    @classmethod
    def from_mapping(cls, value: object, index: int) -> OpenQuestion:
        path = f"insights.json::open_questions[{index}]"
        mapping = _strict_mapping(value, path, allowed=cls._fields, required=cls._fields)
        return cls(**{name: _text(mapping[name], f"{path}.{name}") for name in cls._fields})


@dataclass(frozen=True, slots=True)
class KeyClaim(_RecordMapping):
    claim: str
    claim_type: str
    evidence: str
    verification_needed: bool
    verification_question: str | None

    _fields: ClassVar[tuple[str, ...]] = (
        "claim",
        "claim_type",
        "evidence",
        "verification_needed",
        "verification_question",
    )

    def __post_init__(self) -> None:
        for name in ("claim", "evidence"):
            object.__setattr__(self, name, _text(getattr(self, name), f"key_claim.{name}"))
        object.__setattr__(
            self,
            "claim_type",
            _enum_text(self.claim_type, CLAIM_TYPES, "key_claim.claim_type"),
        )
        if not isinstance(self.verification_needed, bool):
            raise _error("key_claim.verification_needed", "must be a boolean")
        object.__setattr__(
            self,
            "verification_question",
            _optional_text(self.verification_question, "key_claim.verification_question"),
        )

    @classmethod
    def from_mapping(cls, value: object, index: int) -> KeyClaim:
        path = f"insights.json::key_claims[{index}]"
        mapping = _strict_mapping(value, path, allowed=cls._fields, required=cls._fields)
        verification_needed = mapping["verification_needed"]
        if not isinstance(verification_needed, bool):
            raise _error(f"{path}.verification_needed", "must be a boolean")
        return cls(
            claim=_text(mapping["claim"], f"{path}.claim"),
            claim_type=_enum_text(mapping["claim_type"], CLAIM_TYPES, f"{path}.claim_type"),
            evidence=_text(mapping["evidence"], f"{path}.evidence"),
            verification_needed=verification_needed,
            verification_question=_optional_text(
                mapping["verification_question"], f"{path}.verification_question"
            ),
        )


@dataclass(frozen=True, slots=True)
class Connection(_RecordMapping):
    concept: str
    connects_to: str
    relationship: str

    _fields: ClassVar[tuple[str, ...]] = ("concept", "connects_to", "relationship")

    def __post_init__(self) -> None:
        for name in self._fields:
            object.__setattr__(self, name, _text(getattr(self, name), f"connection.{name}"))

    @classmethod
    def from_mapping(cls, value: object, index: int) -> Connection:
        path = f"insights.json::connections[{index}]"
        mapping = _strict_mapping(value, path, allowed=cls._fields, required=cls._fields)
        return cls(**{name: _text(mapping[name], f"{path}.{name}") for name in cls._fields})


@dataclass(frozen=True, slots=True)
class SourceInsights(_RecordMapping):
    """All nine source insight sections, with V1 implicit and V2 explicit versions."""

    core_insights: tuple[CoreInsight, ...]
    deep_dives: tuple[DeepDive, ...]
    article_ideas: tuple[ArticleIdea, ...]
    project_ideas: tuple[ProjectIdea, ...]
    architectural_implications: tuple[ArchitecturalImplication, ...]
    tradeoffs_and_failure_modes: tuple[TradeoffAndFailureMode, ...]
    open_questions: tuple[OpenQuestion, ...]
    key_claims: tuple[KeyClaim, ...]
    connections: tuple[Connection, ...]
    tags: tuple[str, ...]
    schema_version: int | None = None

    _fields: ClassVar[tuple[str, ...]] = INSIGHT_SECTIONS + ("tags",)

    def _mapping_fields(self) -> tuple[str, ...]:
        if self.schema_version is None:
            return self._fields
        return self._fields + ("schema_version",)

    def __post_init__(self) -> None:
        expected = {
            "core_insights": CoreInsight,
            "deep_dives": DeepDive,
            "article_ideas": ArticleIdea,
            "project_ideas": ProjectIdea,
            "architectural_implications": ArchitecturalImplication,
            "tradeoffs_and_failure_modes": TradeoffAndFailureMode,
            "open_questions": OpenQuestion,
            "key_claims": KeyClaim,
            "connections": Connection,
        }
        for name, item_type in expected.items():
            values = _tuple(getattr(self, name), f"insights.{name}")
            if any(not isinstance(item, item_type) for item in values):
                raise _error(f"insights.{name}", f"must contain only {item_type.__name__} values")
            object.__setattr__(self, name, values)
        object.__setattr__(
            self, "tags", _strings(_tuple(self.tags, "insights.tags"), "insights.tags")
        )
        version = _schema_version(self.schema_version, "insights.schema_version")
        object.__setattr__(self, "schema_version", version)

    @classmethod
    def from_mapping(cls, value: object, path: str = "insights.json") -> SourceInsights:
        allowed = cls._fields + ("schema_version",)
        mapping = _strict_mapping(value, path, allowed=allowed, required=cls._fields)
        return cls(
            core_insights=tuple(
                CoreInsight.from_mapping(item, index)
                for index, item in enumerate(
                    _parsed_sequence(mapping["core_insights"], f"{path}::core_insights")
                )
            ),
            deep_dives=tuple(
                DeepDive.from_mapping(item, index)
                for index, item in enumerate(
                    _parsed_sequence(mapping["deep_dives"], f"{path}::deep_dives")
                )
            ),
            article_ideas=tuple(
                ArticleIdea.from_mapping(item, index)
                for index, item in enumerate(
                    _parsed_sequence(mapping["article_ideas"], f"{path}::article_ideas")
                )
            ),
            project_ideas=tuple(
                ProjectIdea.from_mapping(item, index)
                for index, item in enumerate(
                    _parsed_sequence(mapping["project_ideas"], f"{path}::project_ideas")
                )
            ),
            architectural_implications=tuple(
                ArchitecturalImplication.from_mapping(item, index)
                for index, item in enumerate(
                    _parsed_sequence(
                        mapping["architectural_implications"],
                        f"{path}::architectural_implications",
                    )
                )
            ),
            tradeoffs_and_failure_modes=tuple(
                TradeoffAndFailureMode.from_mapping(item, index)
                for index, item in enumerate(
                    _parsed_sequence(
                        mapping["tradeoffs_and_failure_modes"],
                        f"{path}::tradeoffs_and_failure_modes",
                    )
                )
            ),
            open_questions=tuple(
                OpenQuestion.from_mapping(item, index)
                for index, item in enumerate(
                    _parsed_sequence(mapping["open_questions"], f"{path}::open_questions")
                )
            ),
            key_claims=tuple(
                KeyClaim.from_mapping(item, index)
                for index, item in enumerate(
                    _parsed_sequence(mapping["key_claims"], f"{path}::key_claims")
                )
            ),
            connections=tuple(
                Connection.from_mapping(item, index)
                for index, item in enumerate(
                    _parsed_sequence(mapping["connections"], f"{path}::connections")
                )
            ),
            tags=_strings(mapping["tags"], f"{path}::tags"),
            schema_version=_schema_version(mapping.get("schema_version"), f"{path}.schema_version"),
        )


@dataclass(frozen=True, slots=True)
class RawVideo:
    """Compatibility source record used by the current loader and normalizer."""

    index: IndexItem
    frontmatter: SourceFrontmatter
    summary_markdown: str
    insights: SourceInsights
    summary_path: str
    insights_path: str
    schema_version: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.index, IndexItem):
            raise _error("raw_video.index", "must be an IndexItem")
        frontmatter = self.frontmatter
        if not isinstance(frontmatter, SourceFrontmatter):
            try:
                frontmatter = SourceFrontmatter.from_mapping(frontmatter)
            except SourceModelError:
                if type(self) is not RawVideo:
                    raise
                frontmatter = _freeze(frontmatter)
        object.__setattr__(self, "frontmatter", frontmatter)
        object.__setattr__(
            self,
            "summary_markdown",
            _text(self.summary_markdown, "raw_video.summary_markdown"),
        )
        insights = self.insights
        if not isinstance(insights, SourceInsights):
            try:
                insights = SourceInsights.from_mapping(insights)
            except SourceModelError:
                if type(self) is not RawVideo:
                    raise
                insights = _freeze(insights)
        object.__setattr__(self, "insights", insights)
        object.__setattr__(
            self,
            "summary_path",
            _text(self.summary_path, "raw_video.summary_path"),
        )
        object.__setattr__(
            self,
            "insights_path",
            _text(self.insights_path, "raw_video.insights_path"),
        )
        explicit_version = _schema_version(self.schema_version, "raw_video.schema_version")
        insight_version = (
            insights.schema_version
            if isinstance(insights, SourceInsights)
            else insights.get("schema_version")
            if isinstance(insights, Mapping)
            else None
        )
        if (
            explicit_version is not None
            and insight_version is not None
            and explicit_version != insight_version
        ):
            raise _error(
                "raw_video.schema_version",
                f"does not agree with insights.schema_version {insight_version}",
            )
        object.__setattr__(
            self,
            "schema_version",
            explicit_version if explicit_version is not None else insight_version,
        )

    @property
    def video_id(self) -> str:
        return self.index.video_id

    @property
    def title(self) -> str:
        return self.index.title

    @property
    def channel(self) -> str:
        return self.index.channel

    @property
    def source_version(self) -> SourceVersion:
        return SourceVersion.V2 if self.schema_version == 2 else SourceVersion.V1

    @classmethod
    def from_mappings(
        cls,
        *,
        index: IndexItem,
        frontmatter: object,
        summary_markdown: str,
        insights: object,
        summary_path: str,
        insights_path: str,
    ) -> RawVideo:
        """Build a record while retaining whether the source version was explicit."""

        typed_insights = (
            insights
            if isinstance(insights, SourceInsights)
            else SourceInsights.from_mapping(insights)
        )
        return cls(
            index=index,
            frontmatter=(
                frontmatter
                if isinstance(frontmatter, SourceFrontmatter)
                else SourceFrontmatter.from_mapping(frontmatter)
            ),
            summary_markdown=summary_markdown,
            insights=typed_insights,
            summary_path=summary_path,
            insights_path=insights_path,
            schema_version=typed_insights.schema_version,
        )


@dataclass(frozen=True, slots=True)
class V1SourceRecord(RawVideo):
    """A V1 record whose absent schema_version means the legacy format."""

    def __post_init__(self) -> None:
        RawVideo.__post_init__(self)
        if self.schema_version is not None or self.insights.schema_version is not None:
            raise _error(
                "v1_source_record.schema_version",
                "must be absent for an implicit V1 record",
            )


@dataclass(frozen=True, slots=True)
class V2SourceRecord(RawVideo):
    """A V2 record with an explicit schema_version of 2."""

    schema_version: int = 2

    def __post_init__(self) -> None:
        RawVideo.__post_init__(self)
        if self.schema_version != 2 or self.insights.schema_version != 2:
            raise _error(
                "v2_source_record.schema_version",
                "must be explicitly set to 2 on the record and insights",
            )


type SourceRecord = V1SourceRecord | V2SourceRecord


@dataclass(frozen=True, slots=True)
class LoadedCorpus:
    """An immutable loaded source corpus."""

    source_root: Path
    index_items: tuple[IndexItem, ...]
    videos: tuple[RawVideo, ...]
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.source_root, Path):
            raise _error("loaded_corpus.source_root", "must be a pathlib.Path")
        index_items = _tuple(self.index_items, "loaded_corpus.index_items")
        if any(not isinstance(item, IndexItem) for item in index_items):
            raise _error("loaded_corpus.index_items", "must contain only IndexItem values")
        videos = _tuple(self.videos, "loaded_corpus.videos")
        if any(not isinstance(video, RawVideo) for video in videos):
            raise _error("loaded_corpus.videos", "must contain only RawVideo values")
        warnings = _strings(
            _tuple(self.warnings, "loaded_corpus.warnings"),
            "loaded_corpus.warnings",
        )
        object.__setattr__(self, "index_items", index_items)
        object.__setattr__(self, "videos", videos)
        object.__setattr__(self, "warnings", warnings)


__all__ = [
    "ARCHITECTURAL_IMPLICATION_FIELDS",
    "CLAIM_TYPES",
    "EVIDENCE_STRENGTHS",
    "INDEX_STATUSES",
    "INSIGHT_SECTIONS",
    "INSIGHT_TYPES",
    "NOVELTIES",
    "PRIORITIES",
    "PROJECT_FITS",
    "ArticleIdea",
    "ArchitecturalImplication",
    "Connection",
    "CoreInsight",
    "DeepDive",
    "EvidenceQuote",
    "IndexItem",
    "KeyClaim",
    "LoadedCorpus",
    "OpenQuestion",
    "ProjectIdea",
    "Quote",
    "RawVideo",
    "SourceFrontmatter",
    "SourceInsights",
    "SourceModelError",
    "SourceRecord",
    "SourceValidationError",
    "SourceVersion",
    "TradeoffAndFailureMode",
    "V1SourceRecord",
    "V2SourceRecord",
]
