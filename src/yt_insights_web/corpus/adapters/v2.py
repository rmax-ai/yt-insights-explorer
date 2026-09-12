"""V2 source-artifact validation and adaptation.

The producer owns the V2 writer and schema files.  The explorer keeps a
small, dependency-free reader here so it can validate the vendored contract
without importing the producer checkout.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from ..identity import claim_fingerprint
from ..normalized_models import (
    Evidence,
    EvidenceAvailability,
    EvidenceOccurrence,
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
from ..source_models import IndexItem, SourceFrontmatter
from ..source_models import V2SourceRecord as SourceV2Record

SCHEMA_VERSION = 2
ARTIFACT_KIND_SUMMARY = "video-summary"
ARTIFACT_KIND_INSIGHTS = "video-insights"
PERSISTED_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]*-v[0-9]+:[^\s:]+(?::[^\s:]+)*$")
TIMESTAMP_PROVENANCE_VALUES = (
    "extracted-by-model",
    "recovered-exact-match",
    "none",
)
CLAIM_TYPE_COMPATIBILITY = {
    "empirical": "factual",
    "practice": "opinion",
}

INSIGHTS_SECTIONS = (
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

SUMMARY_REQUIRED = (
    "schema_version",
    "artifact_kind",
    "video_id",
    "evidence",
    "overview",
    "topic_map",
    "key_points",
    "frameworks",
    "examples",
    "takeaways",
    "claims_to_verify",
    "quotes",
    "compressed",
)
INSIGHTS_REQUIRED = (
    "schema_version",
    "artifact_kind",
    "video_id",
    "evidence",
    "topics",
    "concept_candidates",
    *INSIGHTS_SECTIONS,
)

_MISSING = object()


class V2ValidationError(ValueError):
    """Raised when one V2 artifact violates the shipped reader contract."""

    def __init__(self, errors: list[str] | tuple[str, ...]):
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


@dataclass(frozen=True, slots=True)
class FrozenMapping(Mapping[str, object]):
    """A recursively immutable mapping used for retained source payloads."""

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

    def get(self, key: str, default: object = None) -> object:
        try:
            return self[key]
        except KeyError:
            return default


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return FrozenMapping(tuple((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _mapping(value: object, path: str, errors: list[str]) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        errors.append(f"{path}: must be an object")
        return None
    if any(not isinstance(key, str) for key in value):
        errors.append(f"{path}: object keys must be strings")
        return None
    return value


def _required(mapping: Mapping[str, object], key: str, path: str, errors: list[str]) -> object:
    if key not in mapping:
        errors.append(f"{path}: missing required field {key!r}")
        return _MISSING
    return mapping[key]


def _closed_mapping(
    value: object,
    path: str,
    *,
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
    errors: list[str],
) -> Mapping[str, object] | None:
    mapping = _mapping(value, path, errors)
    if mapping is None:
        return None
    allowed = set(required) | set(optional)
    unknown = sorted(key for key in mapping if key not in allowed)
    if unknown:
        errors.append(f"{path}: unknown field(s): {', '.join(repr(key) for key in unknown)}")
    for key in required:
        _required(mapping, key, path, errors)
    return mapping


def _text(value: object, path: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not value:
        errors.append(f"{path}: must be a non-empty string")
        return None
    return value


def _video_id(value: object, path: str, errors: list[str]) -> str | None:
    result = _text(value, path, errors)
    if result is not None and (any(char.isspace() for char in result) or ":" in result):
        errors.append(f"{path}: must not contain whitespace or ':'")
        return None
    return result


def _persisted_id(
    value: object,
    path: str,
    errors: list[str],
    *,
    video_id: str | None,
) -> str | None:
    result = _text(value, path, errors)
    if result is None:
        return None
    if PERSISTED_ID_PATTERN.fullmatch(result) is None:
        errors.append(f"{path}: must be a versioned persisted ID")
        return None
    if video_id is not None:
        _, _, payload = result.partition(":")
        if not payload.startswith(f"{video_id}:"):
            errors.append(
                f"{path}: persisted ID payload does not match video_id {video_id!r}"
            )
    return result


def _number_or_null(value: object, path: str, errors: list[str]) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{path}: must be a number or null")
        return None
    result = float(value)
    if not math.isfinite(result) or result < 0:
        errors.append(f"{path}: must be a finite non-negative number or null")
        return None
    return result


def _string_or_null(value: object, path: str, errors: list[str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        errors.append(f"{path}: must be a string or null")
        return None
    return value


def _list(value: object, path: str, errors: list[str]) -> list[object] | None:
    if not isinstance(value, list):
        errors.append(f"{path}: must be a list")
        return None
    return value


def _validate_timestamp(
    timestamp: float | None,
    method: object,
    path: str,
    errors: list[str],
) -> str | None:
    if method not in TIMESTAMP_PROVENANCE_VALUES:
        errors.append(
            f"{path}.timestamp_method: unknown value {method!r}; "
            f"expected one of {', '.join(TIMESTAMP_PROVENANCE_VALUES)}"
        )
        return None
    if timestamp is None and method != "none":
        errors.append(f"{path}: timestamp_method must be 'none' when timestamp_seconds is null")
    if timestamp is not None and method == "none":
        errors.append(
            f"{path}: timestamp_method must explain a non-null timestamp_seconds"
        )
    return method


def _youtube_video_id(source_url: str) -> str | None:
    parsed = urlparse(source_url)
    host = (parsed.hostname or "").lower()
    if host == "youtu.be":
        return parsed.path.lstrip("/").split("/", 1)[0] or None
    if host.endswith(("youtube.com", "youtube-nocookie.com")):
        value = parse_qs(parsed.query).get("v", [None])[0]
        if value:
            return value
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] in {"embed", "shorts", "live"}:
            return parts[1]
    return None


def _validate_provenance(
    value: object,
    path: str,
    errors: list[str],
) -> tuple[str | None, str | None]:
    mapping = _closed_mapping(
        value,
        path,
        required=("origin", "timestamp_method"),
        errors=errors,
    )
    if mapping is None:
        return None, None
    origin = _text(mapping.get("origin"), f"{path}.origin", errors)
    method = mapping.get("timestamp_method", _MISSING)
    return origin, method if isinstance(method, str) else None


def _validate_evidence(
    value: object,
    path: str,
    errors: list[str],
    *,
    video_id: str | None,
) -> tuple[str | None, dict[str, object]]:
    mapping = _closed_mapping(
        value,
        path,
        required=("id", "text", "timestamp_seconds", "source_url", "provenance"),
        errors=errors,
    )
    if mapping is None:
        return None, {}
    evidence_id = _persisted_id(mapping.get("id"), f"{path}.id", errors, video_id=video_id)
    text = _text(mapping.get("text"), f"{path}.text", errors)
    timestamp = _number_or_null(
        mapping.get("timestamp_seconds"), f"{path}.timestamp_seconds", errors
    )
    source_url = _string_or_null(mapping.get("source_url"), f"{path}.source_url", errors)
    _, method = _validate_provenance(mapping.get("provenance"), f"{path}.provenance", errors)
    _validate_timestamp(timestamp, method, f"{path}.provenance", errors)
    if source_url is not None and video_id is not None:
        referenced_video_id = _youtube_video_id(source_url)
        if referenced_video_id is not None and referenced_video_id != video_id:
            errors.append(
                f"{path}.source_url: video_id {referenced_video_id!r} "
                f"does not match document video_id {video_id!r}"
            )
    return evidence_id, {
        "id": evidence_id,
        "text": text,
        "timestamp_seconds": timestamp,
        "source_url": source_url,
        "timestamp_method": method,
        "provenance": mapping.get("provenance"),
    }


def _validate_ref_list(
    value: object,
    path: str,
    errors: list[str],
    *,
    video_id: str | None,
) -> tuple[str, ...]:
    values = _list(value, path, errors)
    if values is None:
        return ()
    refs: list[str] = []
    for index, reference in enumerate(values):
        parsed = _persisted_id(
            reference,
            f"{path}[{index}]",
            errors,
            video_id=video_id,
        )
        if parsed is not None:
            refs.append(parsed)
    return tuple(refs)


def _validate_persisted_text(
    value: object,
    path: str,
    errors: list[str],
    *,
    video_id: str | None,
) -> None:
    mapping = _closed_mapping(value, path, required=("id", "value"), errors=errors)
    if mapping is None:
        return
    _persisted_id(mapping.get("id"), f"{path}.id", errors, video_id=video_id)
    _text(mapping.get("value"), f"{path}.value", errors)


def _validate_labeled_record(
    value: object,
    path: str,
    errors: list[str],
    *,
    video_id: str | None,
) -> None:
    mapping = _closed_mapping(
        value,
        path,
        required=("id", "label", "origin"),
        errors=errors,
    )
    if mapping is None:
        return
    _persisted_id(mapping.get("id"), f"{path}.id", errors, video_id=video_id)
    _text(mapping.get("label"), f"{path}.label", errors)
    _text(mapping.get("origin"), f"{path}.origin", errors)


def _validate_section_item(
    value: object,
    path: str,
    section: str,
    errors: list[str],
    *,
    video_id: str | None,
) -> tuple[str | None, tuple[str, ...]]:
    fields: dict[str, tuple[str, ...]] = {
        "core_insights": (
            "id",
            "evidence_refs",
            "insight",
            "type",
            "why_it_matters",
            "generalization",
            "evidence_strength",
            "novelty",
        ),
        "deep_dives": (
            "id",
            "evidence_refs",
            "topic",
            "research_question",
            "why",
            "trigger_insight",
            "priority",
        ),
        "article_ideas": (
            "id",
            "evidence_refs",
            "title",
            "thesis",
            "angle",
            "based_on",
            "audience",
        ),
        "project_ideas": (
            "id",
            "evidence_refs",
            "name",
            "hypothesis",
            "poc",
            "measurement",
            "based_on",
            "raw_fit",
        ),
        "architectural_implications": (
            "id",
            "evidence_refs",
            "observation",
            "before",
            "after",
            "consequence",
        ),
        "tradeoffs_and_failure_modes": (
            "id",
            "evidence_refs",
            "topic",
            "benefit",
            "cost_or_risk",
        ),
        "open_questions": (
            "id",
            "evidence_refs",
            "question",
            "why_unresolved",
            "research_direction",
        ),
        "key_claims": (
            "id",
            "evidence_refs",
            "claim",
            "claim_type",
            "verification_requested",
            "verification_question",
        ),
        "connections": (
            "id",
            "evidence_refs",
            "concept",
            "connects_to",
            "relationship",
        ),
    }
    mapping = _closed_mapping(value, path, required=fields[section], errors=errors)
    if mapping is None:
        return None, ()
    item_id = _persisted_id(mapping.get("id"), f"{path}.id", errors, video_id=video_id)
    refs = _validate_ref_list(
        mapping.get("evidence_refs"),
        f"{path}.evidence_refs",
        errors,
        video_id=video_id,
    )
    for field in fields[section]:
        if field in {"id", "evidence_refs", "verification_requested", "verification_question"}:
            continue
        _text(mapping.get(field), f"{path}.{field}", errors)
    if section == "key_claims":
        if not isinstance(mapping.get("verification_requested"), bool):
            errors.append(f"{path}.verification_requested: must be a boolean")
        _string_or_null(
            mapping.get("verification_question"),
            f"{path}.verification_question",
            errors,
        )
    return item_id, refs


def _validate_summary_payload(
    payload: object,
    path: str,
    errors: list[str],
    *,
    expected_video_id: str | None,
) -> tuple[str | None, set[str]]:
    mapping = _closed_mapping(payload, path, required=SUMMARY_REQUIRED, errors=errors)
    if mapping is None:
        return None, set()
    version = mapping.get("schema_version")
    if version != SCHEMA_VERSION:
        errors.append(f"{path}.schema_version: expected 2, got {version!r}")
    artifact_kind = mapping.get("artifact_kind")
    if artifact_kind != ARTIFACT_KIND_SUMMARY:
        errors.append(
            f"{path}.artifact_kind: expected {ARTIFACT_KIND_SUMMARY!r}, got {artifact_kind!r}"
        )
    video_id = _video_id(mapping.get("video_id"), f"{path}.video_id", errors)
    if expected_video_id is not None and video_id is not None and video_id != expected_video_id:
        errors.append(
            f"{path}.video_id: {video_id!r} does not agree with indexed video_id "
            f"{expected_video_id!r}"
        )
    evidence_ids, _ = _validate_evidence_table(
        mapping.get("evidence"),
        f"{path}.evidence",
        errors,
        video_id=video_id,
    )
    _validate_summary_sections(mapping, path, errors, video_id=video_id)
    _validate_all_refs(mapping, path, errors, evidence_ids=evidence_ids)
    return video_id, evidence_ids


def _validate_insights_payload(
    payload: object,
    path: str,
    errors: list[str],
    *,
    expected_video_id: str | None,
) -> tuple[str | None, set[str]]:
    mapping = _closed_mapping(payload, path, required=INSIGHTS_REQUIRED, errors=errors)
    if mapping is None:
        return None, set()
    version = mapping.get("schema_version")
    if version != SCHEMA_VERSION:
        errors.append(f"{path}.schema_version: expected 2, got {version!r}")
    artifact_kind = mapping.get("artifact_kind")
    if artifact_kind != ARTIFACT_KIND_INSIGHTS:
        errors.append(
            f"{path}.artifact_kind: expected {ARTIFACT_KIND_INSIGHTS!r}, got {artifact_kind!r}"
        )
    video_id = _video_id(mapping.get("video_id"), f"{path}.video_id", errors)
    if expected_video_id is not None and video_id is not None and video_id != expected_video_id:
        errors.append(
            f"{path}.video_id: {video_id!r} does not agree with indexed video_id "
            f"{expected_video_id!r}"
        )
    evidence_ids, _ = _validate_evidence_table(
        mapping.get("evidence"),
        f"{path}.evidence",
        errors,
        video_id=video_id,
    )
    for section in ("topics", "concept_candidates"):
        values = _list(mapping.get(section), f"{path}.{section}", errors)
        if values is not None:
            for index, item in enumerate(values):
                _validate_labeled_record(
                    item,
                    f"{path}.{section}[{index}]",
                    errors,
                    video_id=video_id,
                )
    for section in INSIGHTS_SECTIONS:
        values = _list(mapping.get(section), f"{path}.{section}", errors)
        if values is not None:
            for index, item in enumerate(values):
                _validate_section_item(
                    item,
                    f"{path}.{section}[{index}]",
                    section,
                    errors,
                    video_id=video_id,
                )
    _validate_all_refs(mapping, path, errors, evidence_ids=evidence_ids)
    return video_id, evidence_ids


def _validate_evidence_table(
    value: object,
    path: str,
    errors: list[str],
    *,
    video_id: str | None,
) -> tuple[set[str], dict[str, dict[str, object]]]:
    values = _list(value, path, errors)
    if values is None:
        return set(), {}
    evidence_ids: set[str] = set()
    records: dict[str, dict[str, object]] = {}
    for index, item in enumerate(values):
        evidence_id, record = _validate_evidence(
            item,
            f"{path}[{index}]",
            errors,
            video_id=video_id,
        )
        if evidence_id is not None:
            if evidence_id in evidence_ids:
                errors.append(f"{path}[{index}].id: duplicate evidence ID {evidence_id!r}")
            evidence_ids.add(evidence_id)
            records[evidence_id] = record
    return evidence_ids, records


def _validate_all_refs(
    payload: Mapping[str, object],
    path: str,
    errors: list[str],
    *,
    evidence_ids: set[str],
) -> None:
    def visit(value: object, current_path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                child_path = f"{current_path}.{key}"
                if key == "evidence_refs":
                    for index, reference in enumerate(child if isinstance(child, list) else ()):
                        if isinstance(reference, str) and reference not in evidence_ids:
                            errors.append(
                                f"{child_path}[{index}]: evidence reference {reference!r} "
                                "does not resolve in the document-local evidence table"
                            )
                else:
                    visit(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{current_path}[{index}]")

    visit(payload, path)


def _validate_unique_persisted_ids(
    payload: Mapping[str, object],
    path: str,
    errors: list[str],
) -> None:
    seen: dict[str, str] = {}

    def visit(value: object, current_path: str) -> None:
        if isinstance(value, Mapping):
            if isinstance(value.get("id"), str):
                persisted_id = value["id"]
                first_path = seen.get(persisted_id)
                if first_path is not None:
                    errors.append(
                        f"{current_path}.id: duplicate persisted id {persisted_id!r}; "
                        f"first seen at {first_path}"
                    )
                else:
                    seen[persisted_id] = f"{current_path}.id"
            for key, child in value.items():
                if key != "evidence_refs":
                    visit(child, f"{current_path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{current_path}[{index}]")

    visit(payload, path)


def _validate_summary_sections(
    mapping: Mapping[str, object],
    path: str,
    errors: list[str],
    *,
    video_id: str | None,
) -> None:
    overview = _closed_mapping(
        mapping.get("overview"),
        f"{path}.overview",
        required=(
            "title",
            "speaker",
            "channel",
            "main_topic",
            "executive_summary",
            "purpose",
        ),
        errors=errors,
    )
    if overview is not None:
        for field in overview:
            _text(overview[field], f"{path}.overview.{field}", errors)

    topic_map = _list(mapping.get("topic_map"), f"{path}.topic_map", errors)
    if topic_map is not None:
        for index, item in enumerate(topic_map):
            item_path = f"{path}.topic_map[{index}]"
            item_mapping = _closed_mapping(
                item,
                item_path,
                required=(
                    "id",
                    "evidence_refs",
                    "topic",
                    "timestamp_range",
                    "explanation",
                    "key_claims",
                    "examples",
                    "terminology",
                    "why_it_matters",
                ),
                errors=errors,
            )
            if item_mapping is None:
                continue
            _persisted_id(item_mapping.get("id"), f"{item_path}.id", errors, video_id=video_id)
            _validate_ref_list(
                item_mapping.get("evidence_refs"),
                f"{item_path}.evidence_refs",
                errors,
                video_id=video_id,
            )
            for field in ("topic", "explanation", "why_it_matters"):
                _text(item_mapping.get(field), f"{item_path}.{field}", errors)
            _string_or_null(
                item_mapping.get("timestamp_range"),
                f"{item_path}.timestamp_range",
                errors,
            )
            for field in ("key_claims", "examples", "terminology"):
                nested = _list(item_mapping.get(field), f"{item_path}.{field}", errors)
                if nested is not None:
                    for nested_index, value in enumerate(nested):
                        _validate_persisted_text(
                            value,
                            f"{item_path}.{field}[{nested_index}]",
                            errors,
                            video_id=video_id,
                        )

    summary_item_specs = {
        "key_points": (
            "id",
            "evidence_refs",
            "point",
            "explanation",
            "evidence",
            "practical_implication",
        ),
        "frameworks": ("id", "evidence_refs", "name", "how_it_works", "components", "when_to_use"),
        "examples": ("id", "evidence_refs", "what_happened", "illustrates", "lesson"),
        "claims_to_verify": ("id", "evidence_refs", "claim", "claim_type"),
    }
    for section, required in summary_item_specs.items():
        values = _list(mapping.get(section), f"{path}.{section}", errors)
        if values is None:
            continue
        for index, item in enumerate(values):
            item_path = f"{path}.{section}[{index}]"
            item_mapping = _closed_mapping(item, item_path, required=required, errors=errors)
            if item_mapping is None:
                continue
            _persisted_id(item_mapping.get("id"), f"{item_path}.id", errors, video_id=video_id)
            _validate_ref_list(
                item_mapping.get("evidence_refs"),
                f"{item_path}.evidence_refs",
                errors,
                video_id=video_id,
            )
            for field in required:
                if field not in {"id", "evidence_refs", "components"}:
                    _text(item_mapping.get(field), f"{item_path}.{field}", errors)
            if section == "frameworks":
                components = _list(
                    item_mapping.get("components"),
                    f"{item_path}.components",
                    errors,
                )
                if components is not None:
                    for nested_index, value in enumerate(components):
                        _validate_persisted_text(
                            value,
                            f"{item_path}.components[{nested_index}]",
                            errors,
                            video_id=video_id,
                        )

    takeaways = _closed_mapping(
        mapping.get("takeaways"),
        f"{path}.takeaways",
        required=("immediate", "strategic", "questions_to_investigate"),
        errors=errors,
    )
    if takeaways is not None:
        for field in takeaways:
            values = _list(takeaways[field], f"{path}.takeaways.{field}", errors)
            if values is not None:
                for index, value in enumerate(values):
                    _validate_persisted_text(
                        value,
                        f"{path}.takeaways.{field}[{index}]",
                        errors,
                        video_id=video_id,
                    )

    quotes = _list(mapping.get("quotes"), f"{path}.quotes", errors)
    if quotes is not None:
        for index, value in enumerate(quotes):
            quote_path = f"{path}.quotes[{index}]"
            quote = _closed_mapping(
                value,
                quote_path,
                required=("id", "text", "timestamp_seconds", "timestamp_provenance"),
                errors=errors,
            )
            if quote is None:
                continue
            _persisted_id(quote.get("id"), f"{quote_path}.id", errors, video_id=video_id)
            _text(quote.get("text"), f"{quote_path}.text", errors)
            timestamp = _number_or_null(
                quote.get("timestamp_seconds"),
                f"{quote_path}.timestamp_seconds",
                errors,
            )
            _validate_timestamp(
                timestamp,
                quote.get("timestamp_provenance"),
                quote_path,
                errors,
            )

    compressed = _closed_mapping(
        mapping.get("compressed"),
        f"{path}.compressed",
        required=("bullets", "keywords", "core_insight"),
        errors=errors,
    )
    if compressed is not None:
        for field in ("bullets", "keywords"):
            values = _list(compressed[field], f"{path}.compressed.{field}", errors)
            if values is not None:
                for index, value in enumerate(values):
                    _validate_persisted_text(
                        value,
                        f"{path}.compressed.{field}[{index}]",
                        errors,
                        video_id=video_id,
                    )
        _text(compressed.get("core_insight"), f"{path}.compressed.core_insight", errors)


def validate_v2_payload(
    payload: Mapping[str, object],
    *,
    artifact_kind: str,
    path: str,
    expected_video_id: str | None = None,
) -> Mapping[str, object]:
    """Validate one shipped V2 JSON payload and return an immutable view."""

    errors: list[str] = []
    if artifact_kind == ARTIFACT_KIND_SUMMARY:
        _validate_summary_payload(payload, path, errors, expected_video_id=expected_video_id)
    elif artifact_kind == ARTIFACT_KIND_INSIGHTS:
        _validate_insights_payload(payload, path, errors, expected_video_id=expected_video_id)
    else:
        raise ValueError(f"unsupported V2 artifact kind {artifact_kind!r}")
    _validate_unique_persisted_ids(payload, path, errors)
    if errors:
        raise V2ValidationError(errors)
    return _freeze(payload)  # type: ignore[return-value]


def validate_v2_summary(
    payload: Mapping[str, object],
    *,
    path: str = "summary.json",
    expected_video_id: str | None = None,
) -> Mapping[str, object]:
    return validate_v2_payload(
        payload,
        artifact_kind=ARTIFACT_KIND_SUMMARY,
        path=path,
        expected_video_id=expected_video_id,
    )


def validate_v2_insights(
    payload: Mapping[str, object],
    *,
    path: str = "insights.json",
    expected_video_id: str | None = None,
) -> Mapping[str, object]:
    return validate_v2_payload(
        payload,
        artifact_kind=ARTIFACT_KIND_INSIGHTS,
        path=path,
        expected_video_id=expected_video_id,
    )


def persisted_evidence_ids(payload: Mapping[str, object]) -> tuple[str, ...]:
    """Return the document-local evidence IDs after validation."""

    values = payload.get("evidence", ())
    return tuple(
        item["id"]
        for item in values
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    )


def _iter_evidence_refs(value: object) -> Iterator[str]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key == "evidence_refs" and isinstance(child, (list, tuple)):
                yield from (item for item in child if isinstance(item, str))
            else:
                yield from _iter_evidence_refs(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_evidence_refs(child)


def evidence_refs(payload: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(_iter_evidence_refs(payload))


@dataclass(frozen=True, slots=True)
class V2EvidenceView(Mapping[str, object]):
    """Compatibility view for normalize.py's legacy evidence projection."""

    id: str
    text: str
    timestamp_seconds: float | None
    source_url: str | None
    timestamp_method: str

    def __getitem__(self, key: str) -> object:
        return {
            "id": self.id,
            "text": self.text,
            "timestamp_seconds": self.timestamp_seconds,
            "source_url": self.source_url,
        }[key]

    def __iter__(self) -> Iterator[str]:
        return iter(("id", "text", "timestamp_seconds", "source_url"))

    def __len__(self) -> int:
        return 4

    def get(self, key: str, default: object = None) -> object:
        try:
            return self[key]
        except KeyError:
            return default


@dataclass(frozen=True, slots=True)
class V2SectionView(Mapping[str, object]):
    """Immutable item view retaining V2 IDs and the shared field names."""

    id: str
    fields: tuple[tuple[str, object], ...]
    evidence_quotes: tuple[V2EvidenceView, ...]

    def __getitem__(self, key: str) -> object:
        if key == "id":
            return self.id
        for name, value in self.fields:
            if name == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (name for name, _ in self.fields)

    def __len__(self) -> int:
        return len(self.fields)

    def get(self, key: str, default: object = None) -> object:
        try:
            return self[key]
        except KeyError:
            return default

    def __getattr__(self, name: str) -> object:
        if name == "evidence":
            return self.evidence_quotes[0] if self.evidence_quotes else None
        if name == "evidence_quote":
            return self.evidence_quotes[0] if self.evidence_quotes else _unavailable_quote()
        if name == "evidence_quotes":
            return object.__getattribute__(self, name)
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _unavailable_quote() -> V2EvidenceView:
    return V2EvidenceView(
        id="evidence-v2:unavailable:placeholder",
        text="Evidence unavailable (no persisted reference).",
        timestamp_seconds=None,
        source_url=None,
        timestamp_method="none",
    )


@dataclass(frozen=True, slots=True)
class V2LabelView:
    id: str
    label: str
    origin: str


@dataclass(frozen=True, slots=True)
class V2InsightsView:
    """The V2 insights document projected for legacy normalizer access."""

    schema_version: int
    evidence: tuple[V2EvidenceView, ...]
    topics: tuple[V2LabelView, ...]
    concept_candidates: tuple[V2LabelView, ...]
    core_insights: tuple[V2SectionView, ...]
    deep_dives: tuple[V2SectionView, ...]
    article_ideas: tuple[V2SectionView, ...]
    project_ideas: tuple[V2SectionView, ...]
    architectural_implications: tuple[V2SectionView, ...]
    tradeoffs_and_failure_modes: tuple[V2SectionView, ...]
    open_questions: tuple[V2SectionView, ...]
    key_claims: tuple[V2SectionView, ...]
    connections: tuple[V2SectionView, ...]
    tags: tuple[str, ...] = ()


def _evidence_views(payload: Mapping[str, object]) -> dict[str, V2EvidenceView]:
    result: dict[str, V2EvidenceView] = {}
    for value in payload.get("evidence", ()):
        if not isinstance(value, Mapping):
            continue
        evidence_id = value.get("id")
        provenance = value.get("provenance")
        method = provenance.get("timestamp_method") if isinstance(provenance, Mapping) else "none"
        if (
            isinstance(evidence_id, str)
            and isinstance(value.get("text"), str)
            and isinstance(method, str)
        ):
            result[evidence_id] = V2EvidenceView(
                id=evidence_id,
                text=value["text"],
                timestamp_seconds=value.get("timestamp_seconds"),
                source_url=value.get("source_url"),
                timestamp_method=method,
            )
    return result


def _section_views(
    payload: Mapping[str, object],
    section: str,
    evidence: dict[str, V2EvidenceView],
) -> tuple[V2SectionView, ...]:
    views: list[V2SectionView] = []
    for raw in payload.get(section, ()):
        if not isinstance(raw, Mapping) or not isinstance(raw.get("id"), str):
            continue
        refs = raw.get("evidence_refs", ())
        resolved = tuple(evidence[ref] for ref in refs if isinstance(ref, str) and ref in evidence)
        values = tuple((key, value) for key, value in raw.items() if key != "id")
        views.append(V2SectionView(raw["id"], values, resolved))
    return tuple(views)


def build_insights_view(payload: Mapping[str, object]) -> V2InsightsView:
    evidence = _evidence_views(payload)
    kwargs: dict[str, object] = {
        "schema_version": 2,
        "evidence": tuple(evidence.values()),
        "topics": tuple(
            V2LabelView(item["id"], item["label"], item["origin"])
            for item in payload.get("topics", ())
            if isinstance(item, Mapping)
        ),
        "concept_candidates": tuple(
            V2LabelView(item["id"], item["label"], item["origin"])
            for item in payload.get("concept_candidates", ())
            if isinstance(item, Mapping)
        ),
    }
    for section in INSIGHTS_SECTIONS:
        kwargs[section] = _section_views(payload, section, evidence)
    return V2InsightsView(**kwargs)  # type: ignore[arg-type]


class V2SourceRecord(SourceV2Record):
    """A loaded V2 bundle retaining both validated JSON documents."""

    __slots__ = (
        "summary_json",
        "insights_json",
        "summary_json_path",
        "summary_schema_version",
        "insights_schema_version",
        "summary_artifact_kind",
        "insights_artifact_kind",
    )

    def __init__(
        self,
        *,
        index: IndexItem,
        frontmatter: SourceFrontmatter,
        summary_markdown: str,
        summary_json: Mapping[str, object] | None,
        insights_json: Mapping[str, object],
        summary_path: str,
        insights_path: str,
        summary_json_path: str | None = None,
    ) -> None:
        object.__setattr__(self, "index", index)
        object.__setattr__(self, "frontmatter", frontmatter)
        object.__setattr__(self, "summary_markdown", summary_markdown)
        object.__setattr__(self, "insights", build_insights_view(insights_json))
        object.__setattr__(self, "summary_path", summary_path)
        object.__setattr__(self, "insights_path", insights_path)
        object.__setattr__(self, "schema_version", 2)
        object.__setattr__(self, "summary_json", summary_json)
        object.__setattr__(self, "insights_json", insights_json)
        object.__setattr__(self, "summary_json_path", summary_json_path)
        object.__setattr__(self, "summary_schema_version", 2 if summary_json is not None else None)
        object.__setattr__(self, "insights_schema_version", 2)
        object.__setattr__(self, "summary_artifact_kind", ARTIFACT_KIND_SUMMARY)
        object.__setattr__(self, "insights_artifact_kind", ARTIFACT_KIND_INSIGHTS)

    @classmethod
    def from_mappings(
        cls,
        *,
        index: IndexItem,
        frontmatter: SourceFrontmatter,
        summary_markdown: str,
        summary_json: Mapping[str, object] | None,
        insights_json: Mapping[str, object],
        summary_path: str,
        insights_path: str,
        summary_json_path: str | None = None,
    ) -> V2SourceRecord:
        return cls(
            index=index,
            frontmatter=frontmatter,
            summary_markdown=summary_markdown,
            summary_json=summary_json,
            insights_json=insights_json,
            summary_path=summary_path,
            insights_path=insights_path,
            summary_json_path=summary_json_path,
        )


def _item_fingerprint(video_id: str, section: str, item_id: str, primary: str) -> str:
    payload = "\x00".join(("item-fingerprint-v2", video_id, section, item_id, primary))
    return "item-fingerprint-v2:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _identity(video_id: str, section: str, item: V2SectionView, primary: str) -> ItemIdentity:
    return ItemIdentity(
        id=item.id,
        kind=IdentityKind.PERSISTED,
        fingerprint=_item_fingerprint(video_id, section, item.id, primary),
        fingerprint_kind=FingerprintKind.ITEM,
    )


def _provenance(source: V2SourceRecord, section: str, index: int) -> Provenance:
    return Provenance(
        source_version=SourceVersion.V2,
        location=f"{source.insights_path}::{section}[{index}]",
        source_index=index,
    )


def _evidence_from_ref(
    source: V2SourceRecord,
    ref: str,
    *,
    section: str,
    item_index: int,
    quote_index: int,
) -> Evidence:
    records = _evidence_views(source.insights_json)
    value = records.get(ref)
    if value is None:
        raise ValueError(
            f"{source.insights_path}::{section}[{item_index}].evidence_refs[{quote_index}]: "
            f"unresolved evidence reference {ref!r}"
        )
    if value.timestamp_seconds is None and value.source_url is None:
        availability = EvidenceAvailability.UNAVAILABLE
        method = None
    elif value.timestamp_method == "recovered-exact-match":
        availability = EvidenceAvailability.RESOLVED_ENRICHMENT
        method = value.timestamp_method
    else:
        availability = EvidenceAvailability.RESOLVED
        method = value.timestamp_method
    evidence_index = next(
        index
        for index, raw in enumerate(source.insights_json.get("evidence", ()))
        if isinstance(raw, Mapping) and raw.get("id") == ref
    )
    return Evidence(
        text=value.text,
        availability=availability,
        provenance=Provenance(
            source_version=SourceVersion.V2,
            location=f"{source.insights_path}::evidence[{evidence_index}]",
            source_index=evidence_index,
        ),
        timestamp_seconds=value.timestamp_seconds,
        source_url=value.source_url,
        resolution_method=method,
        content_id=value.id,
        occurrence=EvidenceOccurrence(section, item_index, quote_index),
        timestamp_method=method,
    )


def _evidence_list(
    source: V2SourceRecord,
    item: V2SectionView,
    section: str,
    index: int,
) -> tuple[Evidence, ...]:
    refs = item.get("evidence_refs", ())
    return tuple(
        _evidence_from_ref(
            source,
            ref,
            section=section,
            item_index=index,
            quote_index=quote_index,
        )
        for quote_index, ref in enumerate(refs)
    )


def _unavailable_evidence(
    source: V2SourceRecord,
    *,
    section: str,
    index: int,
) -> Evidence:
    return Evidence(
        text="Evidence unavailable (no persisted reference).",
        availability=EvidenceAvailability.UNAVAILABLE,
        provenance=_provenance(source, section, index),
        content_id=None,
        occurrence=EvidenceOccurrence(section, index, 0),
    )


def _claim_type(value: str) -> str:
    """Project open V2 claim labels into the legacy renderer enum."""

    return CLAIM_TYPE_COMPATIBILITY.get(value, value)


def _labels(source: V2SourceRecord) -> tuple[RawLabel, ...]:
    frontmatter = tuple(
        RawLabel(
            label=label,
            origin=LabelOrigin.SUMMARY_FRONTMATTER,
            location=f"{source.summary_path}::frontmatter.tags[{index}]",
        )
        for index, label in enumerate(source.frontmatter.tags)
    )
    topics = tuple(
        RawLabel(
            label=item.label,
            origin=LabelOrigin.SOURCE_ARTIFACT,
            location=f"{source.insights_path}::topics[{index}]",
        )
        for index, item in enumerate(source.insights.topics)
    )
    concepts = tuple(
        RawLabel(
            label=item.label,
            origin=LabelOrigin.SOURCE_ARTIFACT,
            location=f"{source.insights_path}::concept_candidates[{index}]",
        )
        for index, item in enumerate(source.insights.concept_candidates)
    )
    return frontmatter + topics + concepts


def _adapt_core(source: V2SourceRecord, index: int, item: V2SectionView) -> NormalizedCoreInsight:
    return NormalizedCoreInsight(
        identity=_identity(source.video_id, "core_insights", item, item.insight),
        provenance=_provenance(source, "core_insights", index),
        insight=item.insight,
        type=item.type,
        why_it_matters=item.why_it_matters,
        generalization=item.generalization,
        evidence=_evidence_list(source, item, "core_insights", index),
        evidence_strength=item.evidence_strength,
        novelty=item.novelty,
    )


def _adapt_deep(source: V2SourceRecord, index: int, item: V2SectionView) -> NormalizedDeepDive:
    return NormalizedDeepDive(
        identity=_identity(source.video_id, "deep_dives", item, item.topic),
        provenance=_provenance(source, "deep_dives", index),
        topic=item.topic,
        research_question=item.research_question,
        why=item.why,
        trigger_insight=item.trigger_insight,
        evidence=_evidence_list(source, item, "deep_dives", index),
        priority=item.priority,
    )


def _adapt_article(
    source: V2SourceRecord,
    index: int,
    item: V2SectionView,
) -> NormalizedArticleIdea:
    return NormalizedArticleIdea(
        identity=_identity(source.video_id, "article_ideas", item, item.title),
        provenance=_provenance(source, "article_ideas", index),
        title=item.title,
        thesis=item.thesis,
        angle=item.angle,
        based_on=item.based_on,
        audience=item.audience,
    )


def _adapt_project(
    source: V2SourceRecord,
    index: int,
    item: V2SectionView,
) -> NormalizedProjectIdea:
    return NormalizedProjectIdea(
        identity=_identity(source.video_id, "project_ideas", item, item.name),
        provenance=_provenance(source, "project_ideas", index),
        name=item.name,
        hypothesis=item.hypothesis,
        poc=item.poc,
        measurement=item.measurement,
        based_on=item.based_on,
        raw_fit=item.raw_fit,
    )


def _adapt_architecture(
    source: V2SourceRecord,
    index: int,
    item: V2SectionView,
) -> NormalizedArchitecturalImplication:
    return NormalizedArchitecturalImplication(
        identity=_identity(source.video_id, "architectural_implications", item, item.observation),
        provenance=_provenance(source, "architectural_implications", index),
        observation=item.observation,
        before=item.before,
        after=item.after,
        consequence=item.consequence,
    )


def _adapt_tradeoff(source: V2SourceRecord, index: int, item: V2SectionView) -> NormalizedTradeoff:
    evidence = _evidence_list(source, item, "tradeoffs_and_failure_modes", index)
    return NormalizedTradeoff(
        identity=_identity(source.video_id, "tradeoffs_and_failure_modes", item, item.topic),
        provenance=_provenance(source, "tradeoffs_and_failure_modes", index),
        topic=item.topic,
        benefit=item.benefit,
        cost_or_risk=item.cost_or_risk,
        evidence=evidence[0]
        if evidence
        else _unavailable_evidence(
            source,
            section="tradeoffs_and_failure_modes",
            index=index,
        ),
    )


def _adapt_question(
    source: V2SourceRecord,
    index: int,
    item: V2SectionView,
) -> NormalizedOpenQuestion:
    return NormalizedOpenQuestion(
        identity=_identity(source.video_id, "open_questions", item, item.question),
        provenance=_provenance(source, "open_questions", index),
        question=item.question,
        why_unresolved=item.why_unresolved,
        research_direction=item.research_direction,
    )


def _adapt_claim(source: V2SourceRecord, index: int, item: V2SectionView) -> NormalizedKeyClaim:
    evidence = _evidence_list(source, item, "key_claims", index)
    return NormalizedKeyClaim(
        identity=_identity(source.video_id, "key_claims", item, item.claim),
        provenance=_provenance(source, "key_claims", index),
        claim=item.claim,
        claim_type=_claim_type(item.claim_type),
        evidence=(
            evidence[0]
            if evidence
            else _unavailable_evidence(source, section="key_claims", index=index)
        ),
        verification_requested=item.verification_requested,
        verification_question=item.verification_question,
        claim_fingerprint=claim_fingerprint(item.claim),
    )


def _adapt_connection(
    source: V2SourceRecord,
    index: int,
    item: V2SectionView,
) -> NormalizedConnection:
    return NormalizedConnection(
        identity=_identity(source.video_id, "connections", item, item.concept),
        provenance=_provenance(source, "connections", index),
        concept=item.concept,
        connects_to=item.connects_to,
        relationship=item.relationship,
        labels=(),
    )


def adapt_v2(source: V2SourceRecord) -> NormalizedVideo:
    """Adapt one validated V2 source record without changing persisted IDs."""

    if not isinstance(source, V2SourceRecord):
        raise TypeError("adapt_v2 expects a V2SourceRecord")
    frontmatter = source.frontmatter
    return NormalizedVideo(
        identity=ItemIdentity(
            id=source.video_id,
            kind=IdentityKind.PERSISTED,
            fingerprint=_item_fingerprint(
                source.video_id,
                "video",
                source.video_id,
                source.video_id,
            ),
            fingerprint_kind=FingerprintKind.ITEM,
        ),
        provenance=Provenance(source_version=SourceVersion.V2, location=source.insights_path),
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
            _adapt_core(source, index, item)
            for index, item in enumerate(source.insights.core_insights)
        ),
        deep_dives=tuple(
            _adapt_deep(source, index, item)
            for index, item in enumerate(source.insights.deep_dives)
        ),
        article_ideas=tuple(
            _adapt_article(source, index, item)
            for index, item in enumerate(source.insights.article_ideas)
        ),
        project_ideas=tuple(
            _adapt_project(source, index, item)
            for index, item in enumerate(source.insights.project_ideas)
        ),
        architectural_implications=tuple(
            _adapt_architecture(source, index, item)
            for index, item in enumerate(source.insights.architectural_implications)
        ),
        tradeoffs_and_failure_modes=tuple(
            _adapt_tradeoff(source, index, item)
            for index, item in enumerate(source.insights.tradeoffs_and_failure_modes)
        ),
        open_questions=tuple(
            _adapt_question(source, index, item)
            for index, item in enumerate(source.insights.open_questions)
        ),
        key_claims=tuple(
            _adapt_claim(source, index, item)
            for index, item in enumerate(source.insights.key_claims)
        ),
        connections=tuple(
            _adapt_connection(source, index, item)
            for index, item in enumerate(source.insights.connections)
        ),
    )


adapt = adapt_v2
adapt_v2_record = adapt_v2


__all__ = [
    "ARTIFACT_KIND_INSIGHTS",
    "ARTIFACT_KIND_SUMMARY",
    "FrozenMapping",
    "INSIGHTS_SECTIONS",
    "PERSISTED_ID_PATTERN",
    "SCHEMA_VERSION",
    "TIMESTAMP_PROVENANCE_VALUES",
    "V2EvidenceView",
    "V2InsightsView",
    "V2SectionView",
    "V2SourceRecord",
    "V2ValidationError",
    "adapt",
    "adapt_v2",
    "adapt_v2_record",
    "build_insights_view",
    "evidence_refs",
    "persisted_evidence_ids",
    "validate_v2_insights",
    "validate_v2_payload",
    "validate_v2_summary",
]
