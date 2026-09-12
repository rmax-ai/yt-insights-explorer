"""Deterministic registry loading and source-label resolution.

Overlay files are read from ``overlay_root/corpus/``.  ``overlay_root`` is
the producer checkout (the source root), never the generated site's tree.
Missing files are treated as empty registries so an old checkout remains
compatible; a file that exists must be a complete, version-1 document.

Registry matching is deliberately conservative.  The exact normalization
algorithm is, in order:

1. Unicode NFC normalization;
2. Unicode casefolding;
3. collapse every Unicode-whitespace run to one ASCII space;
4. strip leading and trailing whitespace.

Hyphens and punctuation are significant and are not rewritten.  Therefore
``"context-engineering"`` and ``"context engineering"`` do not collide
unless a registry explicitly makes them the same label.

Every resolved occurrence keeps its raw source value, origin, and exact
location.  A registry match supplies a stable registry ID and an ID-owned
URL.  An unresolved concept deliberately uses the old ``slug.py``
name-derived ID and URL instead.  This split is required for empty overlays
to remain byte-compatible with the V1 output; only registry-mapped nodes
adopt stable registry IDs.

This module owns overlay I/O and pure in-memory resolution only.  It does not
wire registries into compilation, rendering, or the build.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from ..slug import concept_id as legacy_concept_id
from ..slug import concept_slug as legacy_concept_slug
from .normalized_models import LabelOrigin, NormalizedVideo, RawLabel
from .source_models import RawVideo

REGISTRY_SCHEMA_VERSION = 1

RegistryKind = Literal["concept", "topic", "project"]
_REGISTRY_KINDS: tuple[RegistryKind, ...] = ("concept", "topic", "project")
_COLLECTIONS: dict[RegistryKind, str] = {
    "concept": "concepts",
    "topic": "topics",
    "project": "projects",
}
_FILENAMES: dict[str, str] = {
    "concept": "concepts.yml",
    "topic": "topics.yml",
    "project": "projects.yml",
    "claims": "claim-verification.json",
}
_DOCUMENT_KEYS: dict[str, str] = {
    "concept": "concepts",
    "concepts": "concepts",
    "concepts.yml": "concepts",
    "topic": "topics",
    "topics": "topics",
    "topics.yml": "topics",
    "project": "projects",
    "projects": "projects",
    "projects.yml": "projects",
    "claims": "claims",
    "claim_verification": "claims",
    "claim-verification": "claims",
    "claim-verification.json": "claims",
}
_ID_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:[_-][a-z0-9]+)*\Z")
_MISSING = object()
_UNKNOWN_LOCATION = "<unknown source>"

MATCH_CANONICAL_NAME = "canonical_name"
MATCH_ALIAS = "alias"
MATCH_UNRESOLVED = "unresolved"
MATCH_SENTINEL = "sentinel"


class RegistryValidationError(ValueError):
    """Raised when one or more overlay documents violate their contracts."""

    def __init__(self, errors: str | Iterable[str]) -> None:
        if isinstance(errors, str):
            values = (errors,)
        else:
            values = tuple(errors)
        ordered = tuple(sorted(set(value for value in values if value)))
        if not ordered:
            ordered = ("registry validation failed",)
        self.errors = ordered
        super().__init__("registry validation failed:\n" + "\n".join(ordered))


# The longer name is useful at callers that validate more than registries.
OverlayValidationError = RegistryValidationError
RegistryError = RegistryValidationError


def normalize_label(value: str) -> str:
    """Return the exact registry lookup normalization.

    NFC is intentionally applied before casefolding.  ``str.split`` uses
    Unicode whitespace semantics and both collapses whitespace runs and
    removes leading/trailing whitespace before the explicit final strip.
    """

    if not isinstance(value, str):
        raise TypeError("registry label must be a string")
    normalized = unicodedata.normalize("NFC", value).casefold()
    return " ".join(normalized.split()).strip()


# Descriptive aliases make the contract easy to discover without creating
# multiple algorithms.
normalize_alias = normalize_label
normalize_registry_label = normalize_label
canonicalize_label = normalize_label
normalize = normalize_label


def _non_empty_text(value: object, path: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path}: must be a non-empty string")
        return None
    return value


def _entry_id(value: object, path: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not value:
        errors.append(f"{path}: id must be a non-empty lowercase stable ID")
        return None
    if value != value.strip() or _ID_PATTERN.fullmatch(value) is None:
        errors.append(
            f"{path}: id {value!r} must match lowercase stable-ID syntax "
            "[a-z][a-z0-9]*(?:[_-][a-z0-9]+)*"
        )
        return None
    return value


def _path_text(path: Path | str) -> str:
    return Path(path).as_posix()


def _entry_location(path: str, collection: str, index: int, entry_id: object) -> str:
    return f"{path}::{collection}[{index}] (id={entry_id!r})"


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    """One immutable, source-located concept, topic, or project entry."""

    id: str
    canonical_name: str
    aliases: tuple[str, ...]
    status: str
    entry_type: str | None
    location: str
    kind: RegistryKind
    source_index: int

    @property
    def type(self) -> str | None:
        """Return the optional registry entry type."""

        return self.entry_type

    @property
    def registry(self) -> str:
        """Return the registry kind as a plural-friendly string."""

        return _COLLECTIONS[self.kind]

    @property
    def labels(self) -> tuple[str, ...]:
        """Return raw canonical name followed by raw aliases."""

        return (self.canonical_name, *self.aliases)

    @property
    def source_path(self) -> str:
        """Return the overlay path portion of :attr:`location`."""

        return self.location.split("::", 1)[0]

    @property
    def entry_location(self) -> str:
        """Compatibility spelling for the source location."""

        return self.location


ConceptEntry = RegistryEntry
TopicEntry = RegistryEntry
ProjectEntry = RegistryEntry


class RegistryEntries(tuple[RegistryEntry, ...]):
    """Tuple-compatible registry entries with deterministic lookup views."""

    @property
    def entries(self) -> RegistryEntries:
        return self

    @property
    def by_id(self) -> dict[str, RegistryEntry]:
        return {entry.id: entry for entry in self}

    @property
    def by_alias(self) -> dict[str, RegistryEntry]:
        result: dict[str, RegistryEntry] = {}
        for entry in self:
            result[normalize_label(entry.canonical_name)] = entry
            for alias in entry.aliases:
                result.setdefault(normalize_label(alias), entry)
        return result

    def __getitem__(self, key: int | slice | str) -> RegistryEntry | tuple[RegistryEntry, ...]:
        if isinstance(key, str):
            return self.by_id[key]
        return super().__getitem__(key)

    def get(self, entry_id: str, default: RegistryEntry | None = None) -> RegistryEntry | None:
        return self.by_id.get(entry_id, default)


ConceptRegistry = RegistryEntries
TopicRegistry = RegistryEntries
ProjectRegistry = RegistryEntries


@dataclass(frozen=True, slots=True)
class ClaimEvidence:
    """One source citation in a claim-review overlay entry."""

    url: str
    note: str


@dataclass(frozen=True, slots=True)
class ClaimReview:
    """A validated, source-located claim review.

    Cross-referencing claim occurrences belongs to E2-T3.  E2-T2 validates
    the shape and retains the review data without attempting that lookup.
    """

    id: str
    claim_refs: tuple[str, ...]
    claim_fingerprint: str
    status: str
    method: str
    evidence: tuple[ClaimEvidence, ...]
    reviewed_at: str
    reviewer: str
    location: str

    @property
    def entry_location(self) -> str:
        return self.location


@dataclass(frozen=True, slots=True)
class ResolvedLabel:
    """One source-label occurrence after concept/topic resolution."""

    raw_label: str
    origin: LabelOrigin
    location: str
    resolved_id: str | None
    canonical_name: str | None
    match_method: str
    url: str
    registry_kind: RegistryKind

    def __post_init__(self) -> None:
        if not isinstance(self.raw_label, str) or not self.raw_label:
            raise ValueError("resolved label raw_label must be a non-empty string")
        if not isinstance(self.location, str) or not self.location:
            raise ValueError("resolved label location must be a non-empty string")
        if not isinstance(self.origin, LabelOrigin):
            object.__setattr__(self, "origin", LabelOrigin(self.origin))
        if self.resolved_id is not None and not isinstance(self.resolved_id, str):
            raise ValueError("resolved label resolved_id must be a string or null")

    @property
    def label(self) -> str:
        """Return the retained raw label."""

        return self.raw_label

    @property
    def raw(self) -> str:
        """Compatibility alias for the retained source value."""

        return self.raw_label

    @property
    def raw_value(self) -> str:
        """Compatibility alias for the retained source value."""

        return self.raw_label

    @property
    def source_location(self) -> str:
        """Compatibility alias for the exact source location."""

        return self.location

    @property
    def match(self) -> str:
        """Compatibility alias for the resolution method."""

        return self.match_method

    @property
    def resolution_method(self) -> str:
        """Compatibility alias for the resolution method."""

        return self.match_method

    @property
    def kind(self) -> RegistryKind:
        """Return the registry kind used for this occurrence."""

        return self.registry_kind

    @property
    def name(self) -> str:
        """Return canonical display text, or the unresolved raw label."""

        return self.canonical_name or self.raw_label

    @property
    def display_name(self) -> str:
        """Compatibility alias for :attr:`name`."""

        return self.name

    @property
    def id(self) -> str:
        """Return the public node ID, stable or legacy depending on resolution."""

        if self.resolved_id is not None:
            return self.resolved_id
        if self.registry_kind == "concept":
            return legacy_concept_id(self.raw_label)
        return f"{self.registry_kind}-{legacy_concept_slug(self.raw_label)}"

    @property
    def registry_id(self) -> str | None:
        """Return the stable overlay ID, null for unresolved nodes."""

        return self.resolved_id

    @property
    def canonical_id(self) -> str | None:
        """Return the stable canonical ID, null for unresolved nodes."""

        return self.resolved_id

    @property
    def concept_id(self) -> str | None:
        """Return the stable or legacy concept ID for concept results."""

        return self.id if self.registry_kind == "concept" else self.resolved_id

    @property
    def topic_id(self) -> str | None:
        """Return the stable or deterministic topic ID for topic results."""

        return self.id if self.registry_kind == "topic" else self.resolved_id

    @property
    def unresolved(self) -> bool:
        return self.resolved_id is None

    @property
    def source(self) -> RawLabel:
        """Reconstruct the E1 raw-label value for typed consumers."""

        return RawLabel(label=self.raw_label, origin=self.origin, location=self.location)

    def to_dict(self) -> dict[str, object]:
        """Return a renderer-friendly projection without changing the source."""

        return {
            "raw_label": self.raw_label,
            "origin": self.origin.value,
            "location": self.location,
            "id": self.id,
            "resolved_id": self.resolved_id,
            "canonical_id": self.resolved_id,
            "canonical_name": self.canonical_name,
            "name": self.name,
            "match_method": self.match_method,
            "url": self.url,
            "registry": self.registry_kind,
        }

    def __getitem__(self, key: str) -> object:
        return self.to_dict()[key]


ResolvedConcept = ResolvedLabel
ResolvedTopic = ResolvedLabel


@dataclass(frozen=True, slots=True)
class ProjectResolution:
    """One project-fit occurrence, including the ``new`` sentinel."""

    raw_fit: str
    project_ref: str | None
    canonical_name: str | None
    match_method: str
    location: str
    origin: LabelOrigin = LabelOrigin.SOURCE_ARTIFACT

    @property
    def raw_label(self) -> str:
        return self.raw_fit

    @property
    def resolved_id(self) -> str | None:
        return self.project_ref

    @property
    def project_id(self) -> str | None:
        return self.project_ref

    @property
    def unresolved(self) -> bool:
        return self.project_ref is None

    def to_dict(self) -> dict[str, object]:
        return {
            "raw_fit": self.raw_fit,
            "project_ref": self.project_ref,
            "project_id": self.project_ref,
            "canonical_name": self.canonical_name,
            "match_method": self.match_method,
            "location": self.location,
            "origin": self.origin.value,
        }

    def __getitem__(self, key: str) -> object:
        return self.to_dict()[key]


@dataclass(frozen=True, slots=True)
class ResolvedConnection:
    """A connection with independently resolved endpoint occurrences."""

    concept: ResolvedLabel
    connects_to: ResolvedLabel
    relationship: str
    location: str

    @property
    def source(self) -> ResolvedLabel:
        return self.concept

    @property
    def target(self) -> ResolvedLabel:
        return self.connects_to


@dataclass(frozen=True, slots=True)
class ResolvedSource:
    """Resolved occurrences collected from one source record."""

    video_id: str | None
    labels: tuple[ResolvedLabel, ...]
    connections: tuple[ResolvedConnection, ...]
    project_fits: tuple[ProjectResolution, ...]

    @property
    def concept_occurrences(self) -> tuple[ResolvedLabel, ...]:
        endpoints = tuple(
            endpoint
            for connection in self.connections
            for endpoint in (connection.concept, connection.connects_to)
        )
        return self.labels + endpoints

    @property
    def projects(self) -> tuple[ProjectResolution, ...]:
        return self.project_fits


@dataclass(frozen=True, slots=True)
class Registries:
    """All independently versioned overlay documents needed by E2-T2."""

    concepts: tuple[ConceptEntry, ...] = ()
    topics: tuple[TopicEntry, ...] = ()
    projects: tuple[ProjectEntry, ...] = ()
    claim_reviews: tuple[ClaimReview, ...] = ()
    overlay_root: Path | None = None

    def __post_init__(self) -> None:
        for name in ("concepts", "topics", "projects"):
            value = getattr(self, name)
            if not isinstance(value, RegistryEntries):
                object.__setattr__(self, name, RegistryEntries(value))

    @property
    def reviews(self) -> tuple[ClaimReview, ...]:
        return self.claim_reviews

    @property
    def concept_entries(self) -> tuple[ConceptEntry, ...]:
        return self.concepts

    @property
    def topic_entries(self) -> tuple[TopicEntry, ...]:
        return self.topics

    @property
    def project_entries(self) -> tuple[ProjectEntry, ...]:
        return self.projects

    @property
    def concept_registry(self) -> RegistryEntries:
        return self.concepts

    @property
    def topic_registry(self) -> RegistryEntries:
        return self.topics

    @property
    def project_registry(self) -> RegistryEntries:
        return self.projects

    def resolve_concept(
        self,
        label: str | RawLabel,
        *,
        origin: LabelOrigin | str | None = None,
        location: str | None = None,
    ) -> ResolvedLabel:
        return resolve_concept(self, label, origin=origin, location=location)

    def resolve_topic(
        self,
        label: str | RawLabel,
        *,
        origin: LabelOrigin | str | None = None,
        location: str | None = None,
    ) -> ResolvedLabel:
        return resolve_topic(self, label, origin=origin, location=location)

    def resolve_project(
        self,
        raw_fit: str,
        *,
        location: str | None = None,
        origin: LabelOrigin | str = LabelOrigin.SOURCE_ARTIFACT,
    ) -> ProjectResolution:
        return resolve_project(self, raw_fit, location=location, origin=origin)

    def resolve_project_fit(
        self,
        raw_fit: str,
        *,
        location: str | None = None,
        origin: LabelOrigin | str = LabelOrigin.SOURCE_ARTIFACT,
    ) -> ProjectResolution:
        return self.resolve_project(raw_fit, location=location, origin=origin)

    def resolve_label(
        self,
        label: str | RawLabel,
        *,
        kind: RegistryKind | str = "concept",
        origin: LabelOrigin | str | None = None,
        location: str | None = None,
    ) -> ResolvedLabel:
        return resolve_label(
            self,
            label,
            kind=kind,
            origin=origin,
            location=location,
        )

    def resolve(
        self,
        label: str | RawLabel,
        *,
        kind: RegistryKind | str = "concept",
        origin: LabelOrigin | str | None = None,
        location: str | None = None,
    ) -> ResolvedLabel:
        return self.resolve_label(
            label,
            kind=kind,
            origin=origin,
            location=location,
        )

    def resolve_source_record(
        self,
        source: RawVideo | NormalizedVideo,
    ) -> ResolvedSource:
        return resolve_source_record(self, source)


RegistrySet = Registries
RegistryBundle = Registries
Registry = Registries


def _registry_entries(registries: Registries, kind: RegistryKind) -> tuple[RegistryEntry, ...]:
    if not isinstance(registries, Registries):
        raise TypeError("registries must be a Registries value")
    return getattr(registries, _COLLECTIONS[kind])


def _coerce_registries(value: Registries | RegistryEntries, kind: RegistryKind) -> Registries:
    if isinstance(value, Registries):
        return value
    if isinstance(value, RegistryEntries):
        return Registries(**{_COLLECTIONS[kind]: value})
    raise TypeError("registry argument must be a Registries value")


def _coerce_raw_label(
    value: str | RawLabel,
    *,
    origin: LabelOrigin | str | None,
    location: str | None,
) -> RawLabel:
    if isinstance(value, RawLabel):
        if origin is None and location is None:
            return value
        return RawLabel(
            label=value.label,
            origin=origin if origin is not None else value.origin,
            location=location if location is not None else value.location,
        )
    if not isinstance(value, str) or not value:
        raise TypeError("label must be a non-empty string or RawLabel")
    return RawLabel(
        label=value,
        origin=origin or LabelOrigin.SOURCE_ARTIFACT,
        location=location or _UNKNOWN_LOCATION,
    )


def _unresolved_url(kind: RegistryKind, raw_label: str) -> str:
    if kind == "concept":
        return f"concepts/{legacy_concept_slug(raw_label)}/index.html"
    return f"{_COLLECTIONS[kind]}/{kind}-{legacy_concept_slug(raw_label)}/index.html"


def _stable_url(kind: RegistryKind, registry_id: str) -> str:
    if kind == "concept":
        return concept_url(registry_id)
    if kind == "topic":
        return topic_url(registry_id)
    return project_url(registry_id)


def _resolve_entry(
    registries: Registries,
    kind: RegistryKind,
    value: str | RawLabel,
    *,
    origin: LabelOrigin | str | None,
    location: str | None,
) -> ResolvedLabel:
    source = _coerce_raw_label(value, origin=origin, location=location)
    normalized = normalize_label(source.label)
    matches_by_id: dict[str, tuple[RegistryEntry, str]] = {}
    for entry in _registry_entries(registries, kind):
        if normalize_label(entry.canonical_name) == normalized:
            matches_by_id[entry.id] = (entry, MATCH_CANONICAL_NAME)
        for alias in entry.aliases:
            if normalize_label(alias) == normalized:
                matches_by_id.setdefault(entry.id, (entry, MATCH_ALIAS))
    matches = list(matches_by_id.values())
    if len(matches) > 1:
        details = sorted(
            f"id={entry.id!r} at {entry.location} ({method})"
            for entry, method in matches
        )
        raise RegistryValidationError(
            f"{_COLLECTIONS[kind]} lookup for {source.label!r} is ambiguous: "
            + "; ".join(details)
        )
    if matches:
        entry, method = matches[0]
        return ResolvedLabel(
            raw_label=source.label,
            origin=source.origin,
            location=source.location,
            resolved_id=entry.id,
            canonical_name=entry.canonical_name,
            match_method=method,
            url=_stable_url(kind, entry.id),
            registry_kind=kind,
        )
    return ResolvedLabel(
        raw_label=source.label,
        origin=source.origin,
        location=source.location,
        resolved_id=None,
        canonical_name=None,
        match_method=MATCH_UNRESOLVED,
        url=_unresolved_url(kind, source.label),
        registry_kind=kind,
    )


def resolve_concept(
    registries: Registries | RegistryEntries | str | RawLabel,
    label: str | RawLabel | Registries | RegistryEntries,
    *,
    origin: LabelOrigin | str | None = None,
    location: str | None = None,
) -> ResolvedLabel:
    """Resolve one concept occurrence without rewriting its raw label."""

    if not isinstance(registries, (Registries, RegistryEntries)):
        registries, label = label, registries
    registries = _coerce_registries(registries, "concept")
    if not isinstance(label, (str, RawLabel)):
        raise TypeError("resolve_concept expects a string or RawLabel")
    return _resolve_entry(
        registries,
        "concept",
        label,
        origin=origin,
        location=location,
    )


def resolve_topic(
    registries: Registries | RegistryEntries | str | RawLabel,
    label: str | RawLabel | Registries | RegistryEntries,
    *,
    origin: LabelOrigin | str | None = None,
    location: str | None = None,
) -> ResolvedLabel:
    """Resolve one topic occurrence without rewriting its raw label."""

    if not isinstance(registries, (Registries, RegistryEntries)):
        registries, label = label, registries
    registries = _coerce_registries(registries, "topic")
    if not isinstance(label, (str, RawLabel)):
        raise TypeError("resolve_topic expects a string or RawLabel")
    return _resolve_entry(
        registries,
        "topic",
        label,
        origin=origin,
        location=location,
    )


def resolve_label(
    registries: Registries | str | RawLabel,
    label: str | RawLabel | Registries,
    *,
    kind: RegistryKind | str = "concept",
    origin: LabelOrigin | str | None = None,
    location: str | None = None,
) -> ResolvedLabel:
    """Resolve a concept or topic label using an explicit registry kind."""

    if kind in {"concept", "concepts"}:
        return resolve_concept(registries, label, origin=origin, location=location)
    if kind in {"topic", "topics"}:
        return resolve_topic(registries, label, origin=origin, location=location)
    raise ValueError("kind must be 'concept' or 'topic'")


def resolve_labels(
    registries: Registries,
    labels: Iterable[str | RawLabel],
    *,
    kind: RegistryKind | str = "concept",
) -> tuple[ResolvedLabel, ...]:
    """Resolve every occurrence in input order, including duplicate labels."""

    if isinstance(labels, (str, RawLabel)):
        labels = (labels,)
    return tuple(resolve_label(registries, label, kind=kind) for label in labels)


resolve_concept_label = resolve_concept
resolve_topic_label = resolve_topic
resolve_concept_occurrence = resolve_concept
resolve_topic_occurrence = resolve_topic


def resolve_project(
    registries: Registries | RegistryEntries | str,
    raw_fit: str | Registries | RegistryEntries,
    *,
    location: str | None = None,
    origin: LabelOrigin | str = LabelOrigin.SOURCE_ARTIFACT,
) -> ProjectResolution:
    """Resolve a project fit while keeping unknown values and ``new``."""

    if not isinstance(registries, (Registries, RegistryEntries)):
        registries, raw_fit = raw_fit, registries
    registries = _coerce_registries(registries, "project")
    if not isinstance(raw_fit, str) or not raw_fit:
        raise TypeError("project raw_fit must be a non-empty string")
    label_origin = LabelOrigin(origin)
    result_location = location or _UNKNOWN_LOCATION
    if normalize_label(raw_fit) == "new":
        return ProjectResolution(
            raw_fit=raw_fit,
            project_ref=None,
            canonical_name=None,
            match_method=MATCH_SENTINEL,
            location=result_location,
            origin=label_origin,
        )
    normalized = normalize_label(raw_fit)
    matches_by_id: dict[str, tuple[RegistryEntry, str]] = {}
    for entry in registries.projects:
        if normalize_label(entry.canonical_name) == normalized:
            matches_by_id[entry.id] = (entry, MATCH_CANONICAL_NAME)
        if any(normalize_label(alias) == normalized for alias in entry.aliases):
            matches_by_id.setdefault(entry.id, (entry, MATCH_ALIAS))
    matches = list(matches_by_id.values())
    if len(matches) > 1:
        details = sorted(f"id={entry.id!r} at {entry.location}" for entry, _ in matches)
        raise RegistryValidationError(
            f"projects lookup for {raw_fit!r} is ambiguous: " + "; ".join(details)
        )
    if matches:
        entry, method = matches[0]
        return ProjectResolution(
            raw_fit=raw_fit,
            project_ref=entry.id,
            canonical_name=entry.canonical_name,
            match_method=method,
            location=result_location,
            origin=label_origin,
        )
    return ProjectResolution(
        raw_fit=raw_fit,
        project_ref=None,
        canonical_name=None,
        match_method=MATCH_UNRESOLVED,
        location=result_location,
        origin=label_origin,
    )


resolve_project_fit = resolve_project
resolve_project_value = resolve_project


def concept_url(registry_id: str | RegistryEntry | ResolvedLabel) -> str:
    """Build the stable URL owned by a resolved concept registry ID."""

    if isinstance(registry_id, (RegistryEntry, ResolvedLabel)):
        if isinstance(registry_id, ResolvedLabel) and registry_id.resolved_id is None:
            raise ValueError("concept URL requires a resolved registry ID")
        registry_id = registry_id.id
    _validate_url_id(registry_id, "concept ID")
    return f"concepts/{registry_id}/index.html"


def topic_url(registry_id: str | RegistryEntry | ResolvedLabel) -> str:
    """Build the stable URL owned by a resolved topic registry ID."""

    if isinstance(registry_id, (RegistryEntry, ResolvedLabel)):
        if isinstance(registry_id, ResolvedLabel) and registry_id.resolved_id is None:
            raise ValueError("topic URL requires a resolved registry ID")
        registry_id = registry_id.id
    _validate_url_id(registry_id, "topic ID")
    return f"topics/{registry_id}/index.html"


def project_url(registry_id: str | RegistryEntry) -> str:
    """Build the stable URL owned by a resolved project registry ID."""

    if isinstance(registry_id, RegistryEntry):
        registry_id = registry_id.id
    _validate_url_id(registry_id, "project ID")
    return f"projects/{registry_id}/index.html"


def _validate_url_id(value: object, name: str) -> None:
    if not isinstance(value, str) or _ID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be a valid lowercase stable ID")


build_concept_url = concept_url
resolved_concept_url = concept_url


def legacy_concept_url(name: str) -> str:
    """Build the pre-overlay, name-derived concept URL."""

    return f"concepts/{legacy_concept_slug(name)}/index.html"


def _field_location(base: str, field: str) -> str:
    return f"{base}.{field}"


def _source_occurrences(
    source: RawVideo | NormalizedVideo,
) -> tuple[
    tuple[RawLabel, ...],
    tuple[tuple[str, str, str, str], ...],
    tuple[tuple[str, str], ...],
    str | None,
]:
    """Return tags, connection endpoints, project fits, and video ID.

    The tuple shapes intentionally retain every list index.  Connection
    endpoints are separate occurrences even when their text is identical.
    """

    if isinstance(source, RawVideo):
        labels = tuple(
            RawLabel(
                label=label,
                origin=LabelOrigin.SUMMARY_FRONTMATTER,
                location=f"{source.summary_path}::frontmatter.tags[{index}]",
            )
            for index, label in enumerate(source.frontmatter.tags)
        ) + tuple(
            RawLabel(
                label=label,
                origin=LabelOrigin.INSIGHTS_EXTRACTION,
                location=f"{source.insights_path}::tags[{index}]",
            )
            for index, label in enumerate(source.insights.tags)
        )
        connections = tuple(
            (
                item.concept,
                item.connects_to,
                item.relationship,
                f"{source.insights_path}::connections[{index}]",
            )
            for index, item in enumerate(source.insights.connections)
        )
        project_fits = tuple(
            (
                item.fits,
                f"{source.insights_path}::project_ideas[{index}].fits",
            )
            for index, item in enumerate(source.insights.project_ideas)
        )
        return labels, connections, project_fits, source.video_id

    labels = tuple(source.labels)
    connections = tuple(
        (
            item.concept,
            item.connects_to,
            item.relationship,
            item.provenance.location,
        )
        for item in source.connections
    )
    project_fits = tuple(
        (
            item.raw_fit,
            f"{item.provenance.location}.fits"
            if not item.provenance.location.endswith(".fits")
            else item.provenance.location,
        )
        for item in source.project_ideas
    )
    return labels, connections, project_fits, source.video_id


def resolve_source_record(
    registries: Registries,
    source: RawVideo | NormalizedVideo,
) -> ResolvedSource:
    """Resolve all source occurrences with field- and index-precise origins."""

    labels, connections, project_fits, video_id = _source_occurrences(source)
    resolved_labels = tuple(resolve_concept(registries, label) for label in labels)
    resolved_connections = tuple(
        ResolvedConnection(
            concept=resolve_concept(
                registries,
                RawLabel(
                    label=concept,
                    origin=LabelOrigin.CONNECTION_ENDPOINT,
                    location=_field_location(location, "concept"),
                ),
            ),
            connects_to=resolve_concept(
                registries,
                RawLabel(
                    label=connects_to,
                    origin=LabelOrigin.CONNECTION_ENDPOINT,
                    location=_field_location(location, "connects_to"),
                ),
            ),
            relationship=relationship,
            location=location,
        )
        for concept, connects_to, relationship, location in connections
    )
    resolved_projects = tuple(
        resolve_project(
            registries,
            raw_fit,
            location=location,
            origin=LabelOrigin.SOURCE_ARTIFACT,
        )
        for raw_fit, location in project_fits
    )
    return ResolvedSource(
        video_id=video_id,
        labels=resolved_labels,
        connections=resolved_connections,
        project_fits=resolved_projects,
    )


resolve_video = resolve_source_record
resolve_source = resolve_source_record
resolve_normalized_source = resolve_source_record


def resolve_records(
    registries: Registries,
    sources: Iterable[RawVideo | NormalizedVideo],
) -> tuple[ResolvedSource, ...]:
    """Resolve source records in input order without deduplicating occurrences."""

    return tuple(resolve_source_record(registries, source) for source in sources)


def _read_document(path: Path, *, is_json: bool) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _MISSING
    except (OSError, UnicodeError) as exc:
        raise RegistryValidationError(f"{_path_text(path)}: cannot read overlay: {exc}") from exc
    try:
        return json.loads(text) if is_json else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise RegistryValidationError(f"{_path_text(path)}: malformed document: {exc}") from exc


def _parse_injected_document(value: object, *, path: Path, is_json: bool) -> object:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value) if is_json else yaml.safe_load(value)
        except (UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
            raise RegistryValidationError(
                f"{_path_text(path)}: malformed document: {exc}"
            ) from exc
    return value


def _document_key(documents: Mapping[str, object], key: str) -> str | None:
    target = _COLLECTIONS.get(key, key)
    for candidate, canonical in _DOCUMENT_KEYS.items():
        if candidate in documents and canonical == target:
            return candidate
    return None


def _load_documents(
    root: Path,
    documents: Mapping[str, object] | None,
) -> dict[str, tuple[Path, object]]:
    result: dict[str, tuple[Path, object]] = {}
    for key, filename in _FILENAMES.items():
        path = root / "corpus" / filename
        if documents is not None:
            supplied_key = _document_key(documents, key)
            if supplied_key is None:
                result[key] = (path, _MISSING)
            else:
                result[key] = (
                    path,
                    _parse_injected_document(
                        documents[supplied_key],
                        path=path,
                        is_json=key == "claims",
                    ),
                )
        else:
            result[key] = (path, _read_document(path, is_json=key == "claims"))
    return result


def _validate_envelope(
    raw: object,
    *,
    path: str,
    collection: str,
    errors: list[str],
) -> Sequence[object] | None:
    if not isinstance(raw, Mapping):
        errors.append(f"{path}: envelope must be an object")
        return None
    allowed = {"schema_version", collection}
    unknown = sorted((key for key in raw if key not in allowed), key=repr)
    if unknown:
        errors.append(
            f"{path}: unknown envelope field(s): {', '.join(repr(key) for key in unknown)}"
        )
    if "schema_version" not in raw:
        errors.append(f"{path}: missing required field 'schema_version'")
    elif type(raw["schema_version"]) is not int:
        errors.append(f"{path}.schema_version: must be integer 1")
    elif raw["schema_version"] != REGISTRY_SCHEMA_VERSION:
        errors.append(
            f"{path}.schema_version: unsupported version {raw['schema_version']!r}; "
            "expected 1"
        )
    if collection not in raw:
        errors.append(f"{path}: missing required collection '{collection}'")
        return None
    values = raw[collection]
    if not isinstance(values, (list, tuple)):
        errors.append(f"{path}.{collection}: must be a list")
        return None
    return values


def _validate_registry_entries(
    raw: object,
    *,
    kind: RegistryKind,
    path: str,
    errors: list[str],
) -> tuple[RegistryEntry, ...]:
    collection = _COLLECTIONS[kind]
    values = _validate_envelope(raw, path=path, collection=collection, errors=errors)
    if values is None:
        return ()
    allowed = {"id", "canonical_name", "aliases", "status", "type"}
    required = {"id", "canonical_name", "aliases", "status"}
    entries: list[RegistryEntry] = []
    for index, value in enumerate(values):
        entry_id = value.get("id", "<missing>") if isinstance(value, Mapping) else "<invalid>"
        location = _entry_location(path, collection, index, entry_id)
        if not isinstance(value, Mapping):
            errors.append(f"{location}: entry must be an object")
            continue
        unknown = sorted((key for key in value if key not in allowed), key=repr)
        if unknown:
            errors.append(
                f"{location}: unknown field(s): {', '.join(repr(key) for key in unknown)}"
            )
        missing = sorted(required - set(value))
        if missing:
            errors.append(f"{location}: missing required field(s): {', '.join(missing)}")
        entry_errors: list[str] = []
        parsed_id = _entry_id(value.get("id"), f"{location}.id", entry_errors)
        canonical_name = _non_empty_text(
            value.get("canonical_name"),
            f"{location}.canonical_name",
            entry_errors,
        )
        status = _non_empty_text(value.get("status"), f"{location}.status", entry_errors)
        entry_type = None
        if "type" in value:
            entry_type = _non_empty_text(value["type"], f"{location}.type", entry_errors)
        aliases_value = value.get("aliases")
        aliases: tuple[str, ...] = ()
        if not isinstance(aliases_value, (list, tuple)):
            entry_errors.append(f"{location}.aliases: must be a list of strings")
        else:
            parsed_aliases: list[str] = []
            for alias_index, alias in enumerate(aliases_value):
                parsed_alias = _non_empty_text(
                    alias,
                    f"{location}.aliases[{alias_index}]",
                    entry_errors,
                )
                if parsed_alias is not None:
                    parsed_aliases.append(parsed_alias)
            aliases = tuple(parsed_aliases)
        if kind == "project" and (
            (canonical_name is not None and normalize_label(canonical_name) == "new")
            or any(normalize_label(alias) == "new" for alias in aliases)
            or (parsed_id == "new")
        ):
            entry_errors.append(
                f"{location}: reserved project sentinel 'new' cannot be a project entry"
            )
        errors.extend(entry_errors)
        if entry_errors or parsed_id is None or canonical_name is None or status is None:
            continue
        entries.append(
            RegistryEntry(
                id=parsed_id,
                canonical_name=canonical_name,
                aliases=aliases,
                status=status,
                entry_type=entry_type,
                location=location,
                kind=kind,
                source_index=index,
            )
        )
    return tuple(entries)


def _validate_string_list(
    value: object,
    *,
    path: str,
    errors: list[str],
    non_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        errors.append(f"{path}: must be a list of strings")
        return ()
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{path}[{index}]: must be a non-empty string")
        else:
            result.append(item)
    if non_empty and not result:
        errors.append(f"{path}: must contain at least one string")
    return tuple(result)


def _validate_claim_reviews(
    raw: object,
    *,
    path: str,
    errors: list[str],
) -> tuple[ClaimReview, ...]:
    values = _validate_envelope(raw, path=path, collection="reviews", errors=errors)
    if values is None:
        return ()
    allowed = {
        "id",
        "claim_refs",
        "claim_fingerprint",
        "status",
        "method",
        "evidence",
        "reviewed_at",
        "reviewer",
        "supersedes",
        "mixed_grouping",
    }
    required = {
        "id",
        "claim_refs",
        "claim_fingerprint",
        "status",
        "method",
        "evidence",
        "reviewed_at",
        "reviewer",
    }
    reviews: list[ClaimReview] = []
    for index, value in enumerate(values):
        review_id = value.get("id", "<missing>") if isinstance(value, Mapping) else "<invalid>"
        location = f"{path}::reviews[{index}] (id={review_id!r})"
        if not isinstance(value, Mapping):
            errors.append(f"{location}: entry must be an object")
            continue
        unknown = sorted((key for key in value if key not in allowed), key=repr)
        if unknown:
            errors.append(
                f"{location}: unknown field(s): {', '.join(repr(key) for key in unknown)}"
            )
        missing = sorted(required - set(value))
        if missing:
            errors.append(f"{location}: missing required field(s): {', '.join(missing)}")
        entry_errors: list[str] = []
        parsed_id = _non_empty_text(value.get("id"), f"{location}.id", entry_errors)
        claim_fingerprint = _non_empty_text(
            value.get("claim_fingerprint"),
            f"{location}.claim_fingerprint",
            entry_errors,
        )
        status = _non_empty_text(value.get("status"), f"{location}.status", entry_errors)
        method = _non_empty_text(value.get("method"), f"{location}.method", entry_errors)
        reviewed_at = _non_empty_text(
            value.get("reviewed_at"),
            f"{location}.reviewed_at",
            entry_errors,
        )
        reviewer = _non_empty_text(value.get("reviewer"), f"{location}.reviewer", entry_errors)
        claim_refs = _validate_string_list(
            value.get("claim_refs"),
            path=f"{location}.claim_refs",
            errors=entry_errors,
            non_empty=True,
        )
        evidence_values = value.get("evidence")
        evidence: list[ClaimEvidence] = []
        if not isinstance(evidence_values, (list, tuple)):
            entry_errors.append(f"{location}.evidence: must be a list")
        else:
            for evidence_index, evidence_value in enumerate(evidence_values):
                evidence_path = f"{location}.evidence[{evidence_index}]"
                if not isinstance(evidence_value, Mapping):
                    entry_errors.append(f"{evidence_path}: entry must be an object")
                    continue
                unknown_evidence = sorted(
                    (key for key in evidence_value if key not in {"url", "note"}),
                    key=repr,
                )
                if unknown_evidence:
                    entry_errors.append(
                        f"{evidence_path}: unknown field(s): "
                        + ", ".join(repr(key) for key in unknown_evidence)
                    )
                url = _non_empty_text(
                    evidence_value.get("url"),
                    f"{evidence_path}.url",
                    entry_errors,
                )
                note = _non_empty_text(
                    evidence_value.get("note"),
                    f"{evidence_path}.note",
                    entry_errors,
                )
                if url is not None and note is not None:
                    evidence.append(ClaimEvidence(url=url, note=note))
        errors.extend(entry_errors)
        if (
            entry_errors
            or parsed_id is None
            or claim_fingerprint is None
            or status is None
            or method is None
            or reviewed_at is None
            or reviewer is None
        ):
            continue
        reviews.append(
            ClaimReview(
                id=parsed_id,
                claim_refs=claim_refs,
                claim_fingerprint=claim_fingerprint,
                status=status,
                method=method,
                evidence=tuple(evidence),
                reviewed_at=reviewed_at,
                reviewer=reviewer,
                location=location,
            )
        )
    return tuple(reviews)


def _validate_duplicate_ids(
    entries: Sequence[RegistryEntry] | Sequence[ClaimReview],
    *,
    kind: str,
) -> list[str]:
    grouped: dict[str, list[str]] = {}
    for entry in entries:
        grouped.setdefault(entry.id, []).append(entry.location)
    errors: list[str] = []
    for entry_id, locations in sorted(grouped.items()):
        if len(locations) < 2:
            continue
        details = "; ".join(
            f"id={entry_id!r} at {location}" for location in sorted(locations)
        )
        errors.append(f"{kind}: duplicate ID conflict between {details}")
    return errors


def _validate_alias_conflicts(
    entries: Sequence[RegistryEntry],
    *,
    kind: RegistryKind,
) -> list[str]:
    labels: dict[str, list[tuple[RegistryEntry, str, str]]] = {}
    for entry in entries:
        labels.setdefault(normalize_label(entry.canonical_name), []).append(
            (entry, "canonical_name", entry.canonical_name)
        )
        for alias in entry.aliases:
            labels.setdefault(normalize_label(alias), []).append((entry, "alias", alias))
    errors: list[str] = []
    for normalized, occurrences in sorted(labels.items()):
        distinct_entries = {entry.id for entry, _, _ in occurrences}
        if len(distinct_entries) < 2:
            continue
        ordered = sorted(
            occurrences,
            key=lambda item: (item[0].id, item[0].location, item[1], item[2]),
        )
        details = "; ".join(
            f"id={entry.id!r} at {entry.location} ({role}={raw!r})"
            for entry, role, raw in ordered
        )
        errors.append(
            f"{_COLLECTIONS[kind]}: normalized label {normalized!r} "
            f"conflict between {details}"
        )
    return errors


def _validate_cross_entry_invariants(
    entries_by_kind: Mapping[RegistryKind, Sequence[RegistryEntry]],
    reviews: Sequence[ClaimReview],
) -> list[str]:
    errors: list[str] = []
    for kind in _REGISTRY_KINDS:
        entries = entries_by_kind[kind]
        errors.extend(_validate_duplicate_ids(entries, kind=_COLLECTIONS[kind]))
        errors.extend(_validate_alias_conflicts(entries, kind=kind))
    errors.extend(_validate_duplicate_ids(reviews, kind="reviews"))
    return errors


def load_registries(
    source_root: str | Path | None = None,
    documents: Mapping[str, object] | None = None,
    *,
    overlay_root: str | Path | None = None,
    source: str | Path | None = None,
    concepts_document: object = _MISSING,
    topics_document: object = _MISSING,
    projects_document: object = _MISSING,
    claim_verification_document: object = _MISSING,
    concept_document: object = _MISSING,
    topic_document: object = _MISSING,
    project_document: object = _MISSING,
    claim_document: object = _MISSING,
) -> Registries:
    """Load and validate all E2 overlay documents.

    ``source_root`` is the producer checkout root.  ``overlay_root`` is an
    explicit spelling for callers that want to emphasize overlay ownership;
    when omitted it defaults to ``source_root``.  Tests and other callers may
    inject parsed documents with ``documents``; in that mode omitted files
    are treated as missing rather than read from disk.
    """

    if documents is not None and not isinstance(documents, Mapping):
        raise RegistryValidationError("documents: must be a mapping of overlay names to documents")
    if source_root is None and source is not None:
        source_root = source
    duplicate_overrides = (
        (concepts_document, concept_document),
        (topics_document, topic_document),
        (projects_document, project_document),
        (claim_verification_document, claim_document),
    )
    if any(
        first is not _MISSING and second is not _MISSING
        for first, second in duplicate_overrides
    ):
        raise RegistryValidationError("load_registries: duplicate document override names")
    if any(
        value is not _MISSING
        for value in (
            concepts_document,
            topics_document,
            projects_document,
            claim_verification_document,
            concept_document,
            topic_document,
            project_document,
            claim_document,
        )
    ):
        if concepts_document is _MISSING:
            concepts_document = concept_document
        if topics_document is _MISSING:
            topics_document = topic_document
        if projects_document is _MISSING:
            projects_document = project_document
        if claim_verification_document is _MISSING:
            claim_verification_document = claim_document
        supplied = dict(documents or {})
        if concepts_document is not _MISSING:
            supplied["concepts"] = concepts_document
        if topics_document is not _MISSING:
            supplied["topics"] = topics_document
        if projects_document is not _MISSING:
            supplied["projects"] = projects_document
        if claim_verification_document is not _MISSING:
            supplied["claims"] = claim_verification_document
        documents = supplied
    root_value = overlay_root if overlay_root is not None else source_root
    root = Path(root_value if root_value is not None else ".")
    loaded = _load_documents(root, documents)
    errors: list[str] = []
    entries_by_kind: dict[RegistryKind, tuple[RegistryEntry, ...]] = {}
    for kind in _REGISTRY_KINDS:
        path, raw = loaded[kind]
        if raw is _MISSING:
            entries_by_kind[kind] = ()
            continue
        entries_by_kind[kind] = _validate_registry_entries(
            raw,
            kind=kind,
            path=_path_text(path),
            errors=errors,
        )
    claims_path, claims_raw = loaded["claims"]
    if claims_raw is _MISSING:
        reviews: tuple[ClaimReview, ...] = ()
    else:
        reviews = _validate_claim_reviews(
            claims_raw,
            path=_path_text(claims_path),
            errors=errors,
        )
    errors.extend(_validate_cross_entry_invariants(entries_by_kind, reviews))
    if errors:
        raise RegistryValidationError(errors)
    return Registries(
        concepts=tuple(
            sorted(entries_by_kind["concept"], key=lambda entry: (entry.id, entry.canonical_name))
        ),
        topics=tuple(
            sorted(entries_by_kind["topic"], key=lambda entry: (entry.id, entry.canonical_name))
        ),
        projects=tuple(
            sorted(entries_by_kind["project"], key=lambda entry: (entry.id, entry.canonical_name))
        ),
        claim_reviews=tuple(sorted(reviews, key=lambda review: (review.id, review.location))),
        overlay_root=root,
    )


load_overlays = load_registries
load_registry = load_registries
load_all_registries = load_registries
load_registry_documents = load_registries


__all__ = [
    "MATCH_ALIAS",
    "MATCH_CANONICAL_NAME",
    "MATCH_SENTINEL",
    "MATCH_UNRESOLVED",
    "REGISTRY_SCHEMA_VERSION",
    "ClaimEvidence",
    "ClaimReview",
    "ConceptEntry",
    "ConceptRegistry",
    "RegistryEntries",
    "OverlayValidationError",
    "ProjectEntry",
    "ProjectRegistry",
    "ProjectResolution",
    "RegistryBundle",
    "RegistryEntry",
    "RegistryError",
    "RegistryKind",
    "Registry",
    "RegistrySet",
    "RegistryValidationError",
    "Registries",
    "ResolvedConcept",
    "ResolvedConnection",
    "ResolvedLabel",
    "ResolvedSource",
    "ResolvedTopic",
    "TopicEntry",
    "TopicRegistry",
    "build_concept_url",
    "canonicalize_label",
    "concept_url",
    "legacy_concept_url",
    "load_overlays",
    "load_all_registries",
    "load_registry_documents",
    "load_registries",
    "load_registry",
    "normalize_alias",
    "normalize_label",
    "normalize",
    "normalize_registry_label",
    "project_url",
    "resolve_concept",
    "resolve_concept_label",
    "resolve_concept_occurrence",
    "resolve_label",
    "resolve_labels",
    "resolve_project",
    "resolve_project_fit",
    "resolve_project_value",
    "resolve_records",
    "resolve_source",
    "resolve_source_record",
    "resolve_normalized_source",
    "resolve_topic",
    "resolve_topic_label",
    "resolve_topic_occurrence",
    "resolve_video",
    "resolved_concept_url",
    "topic_url",
]
