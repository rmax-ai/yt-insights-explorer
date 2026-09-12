"""Read and validate the source checkout without retaining host paths.

Version detection is per machine-readable artifact.  A present
``schema_version`` is authoritative; an absent value means V1.  Detection
always happens before V1 field validation so an unsupported artifact cannot
silently pass through the permissive legacy validator.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .corpus.adapters.v2 import (
    ARTIFACT_KIND_INSIGHTS,
    ARTIFACT_KIND_SUMMARY,
    V2SourceRecord,
    V2ValidationError,
    validate_v2_insights,
    validate_v2_summary,
)
from .corpus.source_models import SourceFrontmatter, SourceModelError, V1SourceRecord
from .frontmatter import FrontMatterError, parse_summary
from .models import (
    CLAIM_TYPES,
    EVIDENCE_STRENGTHS,
    INDEX_STATUSES,
    INSIGHT_SECTIONS,
    INSIGHT_TYPES,
    NOVELTIES,
    PRIORITIES,
    PROJECT_FITS,
    IndexItem,
    LoadedCorpus,
    RawVideo,
)


@dataclass(frozen=True, slots=True)
class CorpusValidationIssue:
    """Structured context for one source-bundle validation failure."""

    code: str
    path: str
    message: str
    video_id: str | None = None
    artifact: str | None = None
    schema_version: object = "unknown"

    def __str__(self) -> str:
        context = []
        if self.video_id is not None:
            context.append(f"video={self.video_id}")
        if self.artifact is not None:
            context.append(f"artifact={self.artifact}")
        context.append(f"schema_version={self.schema_version}")
        return f"{self.path}: {self.message} [{', '.join(context)}]"


class CorpusValidationError(ValueError):
    """Raised after all independent source validation errors are collected."""

    def __init__(self, errors: Iterable[str | CorpusValidationIssue]):
        issues: list[CorpusValidationIssue] = []
        rendered: list[str] = []
        for error in errors:
            if isinstance(error, CorpusValidationIssue):
                issues.append(error)
                rendered.append(str(error))
            else:
                issues.append(CorpusValidationIssue(code="validation", path="", message=error))
                rendered.append(error)
        self.issues = tuple(issues)
        self.errors = tuple(rendered)
        message = "corpus validation failed:\n" + "\n".join(f" - {error}" for error in self.errors)
        super().__init__(message)


def _location(path: Path, field: str | None = None) -> str:
    return f"{path.as_posix()}{f'::{field}' if field else ''}"


def _required(mapping: dict[str, Any], key: str, location: str, errors: list[str]) -> Any:
    if key not in mapping:
        errors.append(f"{location}: missing required field {key!r}")
        return None
    return mapping[key]


def _string(
    value: Any, location: str, errors: list[str], *, allow_empty: bool = False
) -> str | None:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        errors.append(f"{location}: must be a non-empty string")
        return None
    return value


def _list(value: Any, location: str, errors: list[str]) -> list[Any] | None:
    if not isinstance(value, list):
        errors.append(f"{location}: must be a list")
        return None
    return value


def _mapping(value: Any, location: str, errors: list[str]) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        errors.append(f"{location}: must be an object")
        return None
    return value


def _enum(value: Any, allowed: tuple[str, ...], location: str, errors: list[str]) -> str | None:
    if not isinstance(value, str):
        errors.append(f"{location}: must be a string enum")
        return None
    if value not in allowed:
        errors.append(f"{location}: unknown enum {value!r}; expected one of {', '.join(allowed)}")
        return None
    return value


def _datetime(value: Any, location: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str):
        errors.append(f"{location}: must be an ISO datetime string")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{location}: invalid ISO datetime {value!r}")
        return None
    if parsed.tzinfo is None:
        errors.append(f"{location}: ISO datetime must include a timezone")
        return None
    return parsed


def _number_or_null(value: Any, location: str, errors: list[str]) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{location}: must be a number or null")
        return None
    if value < 0:
        errors.append(f"{location}: must not be negative")
        return None
    return float(value)


def _safe_artifact_path(
    source: Path, value: Any, location: str, errors: list[str]
) -> tuple[Path, str] | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{location}: must be a non-empty artifact path")
        return None
    source = source.resolve()
    artifacts_root = (source / "artifacts").resolve()
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        if candidate.parts and candidate.parts[0] == "artifacts":
            candidate = source / candidate
        else:
            candidate = artifacts_root / candidate
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(artifacts_root)
    except ValueError:
        errors.append(f"{location}: path must stay beneath source artifacts")
        return None
    relative = resolved.relative_to(source).as_posix()
    if not resolved.is_file():
        errors.append(f"{location}: artifact does not exist: {relative}")
        return None
    return resolved, relative


def _read_json(
    path: Path,
    location: str,
    errors: list[object],
    *,
    video_id: str | None = None,
    artifact: str | None = None,
) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(
            CorpusValidationIssue(
                code="invalid_json",
                path=location,
                message=f"cannot read JSON: {exc}",
                video_id=video_id,
                artifact=artifact,
                schema_version="unknown",
            )
        )
        return None


def _detect_artifact_version(
    data: Any,
    location: str,
    errors: list[object],
    *,
    video_id: str | None = None,
    artifact: str | None = None,
    artifact_kind: str | None = None,
    supported_versions: tuple[int, ...] = (1,),
    require_object: bool = False,
    require_version_field: bool = False,
) -> int | None:
    """Select one version for one artifact before running V1 validation."""

    if not isinstance(data, dict):
        if require_object:
            errors.append(
                CorpusValidationIssue(
                    code="missing_metadata",
                    path=location,
                    message="structured artifact must be an object with schema_version",
                    video_id=video_id,
                    artifact=artifact,
                    schema_version="unknown",
                )
            )
            return None
        return 1
    if "schema_version" not in data:
        if require_version_field:
            errors.append(
                CorpusValidationIssue(
                    code="missing_metadata",
                    path=f"{location}.schema_version",
                    message="missing schema_version for structured artifact",
                    video_id=video_id,
                    artifact=artifact,
                    schema_version="missing",
                )
            )
            return None
        return 1
    version = data["schema_version"]
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version not in supported_versions
    ):
        errors.append(
            CorpusValidationIssue(
                code="unsupported_schema_version",
                path=location,
                message=(
                    f"unsupported schema version {version!r}; "
                    "expected numeric version "
                    f"{', '.join(str(item) for item in supported_versions)}"
                ),
                video_id=video_id,
                artifact=artifact,
                schema_version=version,
            )
        )
        return None
    if version == 2 and artifact_kind in {ARTIFACT_KIND_INSIGHTS, ARTIFACT_KIND_SUMMARY}:
        expected_kind = (
            ARTIFACT_KIND_INSIGHTS
            if artifact_kind == ARTIFACT_KIND_INSIGHTS
            else ARTIFACT_KIND_SUMMARY
        )
        if data.get("artifact_kind") != expected_kind:
            errors.append(
                CorpusValidationIssue(
                    code="unsupported_schema_version",
                    path=location,
                    message=(
                        f"unsupported schema version 2: artifact_kind must be "
                        f"{expected_kind!r}, got {data.get('artifact_kind')!r}"
                    ),
                    video_id=video_id,
                artifact=artifact,
                    schema_version=version,
                )
            )
            return None
    return version


def _validate_quote(value: Any, location: str, errors: list[str]) -> None:
    if isinstance(value, str):
        return
    quote = _mapping(value, location, errors)
    if quote is None:
        return
    _string(_required(quote, "text", location, errors), f"{location}.text", errors)
    for optional in ("timestamp_seconds", "source_url"):
        if (
            optional in quote
            and quote[optional] is not None
            and not isinstance(
                quote[optional], (int, float) if optional == "timestamp_seconds" else str
            )
        ):
            errors.append(f"{location}.{optional}: must be the expected scalar or null")


def _validate_insight_item(item: Any, section: str, index: int, errors: list[str]) -> None:
    location = f"insights.json::{section}[{index}]"
    mapping = _mapping(item, location, errors)
    if mapping is None:
        return
    required_fields: dict[str, tuple[str, ...]] = {
        "core_insights": (
            "insight",
            "type",
            "why_it_matters",
            "generalization",
            "evidence_quotes",
            "evidence_strength",
            "novelty",
        ),
        "deep_dives": (
            "topic",
            "research_question",
            "why",
            "trigger_insight",
            "evidence_quotes",
            "priority",
        ),
        "article_ideas": ("title", "thesis", "angle", "based_on", "audience"),
        "project_ideas": ("name", "hypothesis", "poc", "measurement", "based_on", "fits"),
        "architectural_implications": ("observation", "before", "after", "consequence"),
        "tradeoffs_and_failure_modes": ("topic", "benefit", "cost_or_risk", "evidence_quote"),
        "open_questions": ("question", "why_unresolved", "research_direction"),
        "key_claims": (
            "claim",
            "claim_type",
            "evidence",
            "verification_needed",
            "verification_question",
        ),
        "connections": ("concept", "connects_to", "relationship"),
    }
    for field in required_fields[section]:
        _required(mapping, field, location, errors)
    string_fields = set(required_fields[section]) - {
        "evidence_quotes",
        "evidence_quote",
        "verification_needed",
        "verification_question",
        "type",
        "evidence_strength",
        "novelty",
        "priority",
        "fits",
    }
    for field in string_fields:
        if field in mapping:
            _string(mapping[field], f"{location}.{field}", errors)
    if section == "core_insights":
        _enum(mapping.get("type"), INSIGHT_TYPES, f"{location}.type", errors)
        _enum(
            mapping.get("evidence_strength"),
            EVIDENCE_STRENGTHS,
            f"{location}.evidence_strength",
            errors,
        )
        _enum(mapping.get("novelty"), NOVELTIES, f"{location}.novelty", errors)
        quotes = _list(mapping.get("evidence_quotes"), f"{location}.evidence_quotes", errors)
        if quotes is not None:
            for quote_index, quote in enumerate(quotes):
                _validate_quote(quote, f"{location}.evidence_quotes[{quote_index}]", errors)
    elif section == "deep_dives":
        _enum(mapping.get("priority"), PRIORITIES, f"{location}.priority", errors)
        quotes = _list(mapping.get("evidence_quotes"), f"{location}.evidence_quotes", errors)
        if quotes is not None:
            for quote_index, quote in enumerate(quotes):
                _validate_quote(quote, f"{location}.evidence_quotes[{quote_index}]", errors)
    elif section == "tradeoffs_and_failure_modes":
        _validate_quote(mapping.get("evidence_quote"), f"{location}.evidence_quote", errors)
    elif section == "key_claims":
        if not isinstance(mapping.get("verification_needed"), bool):
            errors.append(f"{location}.verification_needed: must be a boolean")
        if mapping.get("verification_question") is not None:
            _string(mapping["verification_question"], f"{location}.verification_question", errors)
        _enum(mapping.get("claim_type"), CLAIM_TYPES, f"{location}.claim_type", errors)
    elif section == "project_ideas":
        _enum(mapping.get("fits"), PROJECT_FITS, f"{location}.fits", errors)


def _validate_insights(data: Any, errors: list[str]) -> dict[str, Any] | None:
    mapping = _mapping(data, "insights.json", errors)
    if mapping is None:
        return None
    for section in INSIGHT_SECTIONS:
        if section not in mapping:
            errors.append(f"insights.json::{section}: missing required top-level section")
            continue
        values = _list(mapping.get(section), f"insights.json::{section}", errors)
        if values is not None:
            for index, item in enumerate(values):
                _validate_insight_item(item, section, index, errors)
    tags = _list(mapping.get("tags"), "insights.json::tags", errors)
    if tags is not None:
        for index, tag in enumerate(tags):
            _string(tag, f"insights.json::tags[{index}]", errors)
    return mapping


def _validate_frontmatter(
    metadata: dict[str, Any],
    item: IndexItem,
    location: str,
    errors: list[str],
    warnings: list[str],
) -> None:
    required = (
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
    for field in required:
        _required(metadata, field, location, errors)
    for field in (
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
        if field in metadata:
            _string(metadata[field], f"{location}.{field}", errors)
    tags = _list(metadata.get("tags"), f"{location}.tags", errors)
    if tags is not None:
        for index, tag in enumerate(tags):
            _string(tag, f"{location}.tags[{index}]", errors)
    _datetime(metadata.get("source_published"), f"{location}.source_published", errors)
    _datetime(metadata.get("captured_at"), f"{location}.captured_at", errors)
    source_type = metadata.get("source_type")
    if source_type != "youtube":
        errors.append(f"{location}.source_type: unknown enum {source_type!r}; expected youtube")
    source_id = metadata.get("id")
    if isinstance(source_id, str) and not source_id.endswith(f":{item.video_id}"):
        errors.append(f"{location}.id: URN does not agree with video id {item.video_id!r}")
    uri = metadata.get("source_uri")
    if isinstance(uri, str):
        parsed = urlparse(uri)
        query_id = parse_qs(parsed.query).get("v", [None])[0]
        if (
            parsed.scheme != "https"
            or parsed.netloc not in {"www.youtube.com", "youtube.com"}
            or parsed.path != "/watch"
        ):
            errors.append(f"{location}.source_uri: expected an https YouTube watch URL")
        elif query_id != item.video_id:
            errors.append(f"{location}.source_uri: video id does not agree with index")
    if isinstance(metadata.get("source_title"), str) and " ".join(
        metadata["source_title"].split()
    ) != " ".join(item.title.split()):
        warnings.append(f"{location}.source_title disagrees with index title for {item.video_id}")
    if isinstance(metadata.get("source_author"), str) and " ".join(
        metadata["source_author"].split()
    ) != " ".join(item.channel.split()):
        warnings.append(
            f"{location}.source_author disagrees with index channel for {item.video_id}"
        )


def _parse_index(
    source: Path, errors: list[object]
) -> tuple[
    list[IndexItem],
    dict[str, tuple[Path, str, Path, str, Path | None, str | None]],
]:
    index_path = source / "index.json"
    data = _read_json(index_path, _location(index_path), errors)
    if data is None:
        return [], {}
    if not isinstance(data, dict):
        errors.append(f"{_location(index_path)}: root must be an object with an items array")
        return [], {}
    items = data.get("items")
    if not isinstance(items, list):
        errors.append(f"{_location(index_path)}: root must contain an items array")
        return [], {}
    index_items: list[IndexItem] = []
    artifact_paths: dict[str, tuple[Path, str, Path, str, Path | None, str | None]] = {}
    for index, raw in enumerate(items):
        location = f"{_location(index_path)}::items[{index}]"
        mapping = _mapping(raw, location, errors)
        if mapping is None:
            continue
        video_id = _string(
            _required(mapping, "video_id", location, errors), f"{location}.video_id", errors
        )
        title = _string(_required(mapping, "title", location, errors), f"{location}.title", errors)
        channel = _string(
            _required(mapping, "channel", location, errors), f"{location}.channel", errors
        )
        status = _enum(
            _required(mapping, "status", location, errors),
            INDEX_STATUSES,
            f"{location}.status",
            errors,
        )
        ingested_at = _datetime(
            _required(mapping, "ingested_at", location, errors), f"{location}.ingested_at", errors
        )
        cost = _number_or_null(mapping.get("cost_usd_total"), f"{location}.cost_usd_total", errors)
        artifacts = _mapping(
            _required(mapping, "artifacts", location, errors), f"{location}.artifacts", errors
        )
        summary_value = artifacts.get("summary") if artifacts else None
        insights_value = artifacts.get("insights") if artifacts else None
        if status == "analyzed":
            summary = _safe_artifact_path(
                source, summary_value, f"{location}.artifacts.summary", errors
            )
            insights = _safe_artifact_path(
                source, insights_value, f"{location}.artifacts.insights", errors
            )
        else:
            summary = insights = None
        if None in (video_id, title, channel, status, ingested_at):
            continue
        item = IndexItem(
            video_id,
            title,
            channel,
            status,
            ingested_at,
            summary[1] if summary else None,
            insights[1] if insights else None,
            cost,
        )
        index_items.append(item)
        if summary and insights:
            summary_json = summary[0].with_name("summary.json")
            summary_json_pair = (
                (summary_json, summary_json.relative_to(source).as_posix())
                if summary[0].name == "summary.md" and summary_json.is_file()
                else (None, None)
            )
            artifact_paths[video_id] = (
                summary[0],
                summary[1],
                insights[0],
                insights[1],
                summary_json_pair[0],
                summary_json_pair[1],
            )
    return index_items, artifact_paths


def load_corpus(source: str | Path) -> LoadedCorpus:
    """Load analyzed records from a source checkout and validate all fields."""

    source_root = Path(source).expanduser().resolve()
    errors: list[object] = []
    warnings: list[str] = []
    index_items, artifact_paths = _parse_index(source_root, errors)
    videos: list[RawVideo] = []
    evidence_ids: dict[str, tuple[str, str]] = {}

    def has_error(path: str, code: str) -> bool:
        return any(
            isinstance(error, CorpusValidationIssue)
            and error.path == path
            and error.code == code
            for error in errors
        )

    def append_v2_errors(
        exception: V2ValidationError,
        *,
        video_id: str,
        artifact: str,
        schema_version: object = 2,
    ) -> None:
        for detail in exception.errors:
            path, separator, message = detail.partition(": ")
            errors.append(
                CorpusValidationIssue(
                    code="invalid_v2_artifact",
                    path=path if separator else artifact,
                    message=message if separator else detail,
                    video_id=video_id,
                    artifact=artifact,
                    schema_version=schema_version,
                )
            )

    def record_evidence_ids(
        payload: object,
        *,
        video_id: str,
        artifact: str,
    ) -> None:
        if not isinstance(payload, Mapping):
            return
        values = payload.get("evidence")
        if not isinstance(values, (list, tuple)):
            return
        for index, value in enumerate(values):
            if not isinstance(value, Mapping) or not isinstance(value.get("id"), str):
                continue
            persisted_id = value["id"]
            previous = evidence_ids.get(persisted_id)
            if previous is not None:
                errors.append(
                    CorpusValidationIssue(
                        code="duplicate_evidence_id",
                        path=f"{artifact}::evidence[{index}].id",
                        message=(
                            f"duplicate evidence ID {persisted_id!r}; first seen at "
                            f"{previous[1]} for video {previous[0]!r}"
                        ),
                        video_id=video_id,
                        artifact=artifact,
                        schema_version=2,
                    )
                )
            else:
                evidence_ids[persisted_id] = (video_id, artifact)

    for item in index_items:
        if item.status != "analyzed":
            continue
        paths = artifact_paths.get(item.video_id)
        if paths is None:
            continue
        (
            summary_path,
            summary_relative,
            insights_path,
            insights_relative,
            summary_json_path,
            summary_json_relative,
        ) = paths
        try:
            parsed = parse_summary(summary_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, FrontMatterError) as exc:
            errors.append(f"{summary_relative}: {exc}")
            continue
        _validate_frontmatter(parsed.metadata, item, summary_relative, errors, warnings)

        summary_json: Any | None = None
        summary_version = 1
        if summary_json_path is not None and summary_json_relative is not None:
            summary_json = _read_json(
                summary_json_path,
                summary_json_relative,
                errors,
                video_id=item.video_id,
                artifact=summary_json_relative,
            )
            if summary_json is None:
                if not has_error(summary_json_relative, "invalid_json"):
                    errors.append(
                        CorpusValidationIssue(
                            code="missing_metadata",
                            path=summary_json_relative,
                            message="JSON value is null; schema_version is unknown",
                            video_id=item.video_id,
                            artifact=summary_json_relative,
                            schema_version="unknown",
                        )
                    )
                continue
            detected_summary_version = _detect_artifact_version(
                summary_json,
                summary_json_relative,
                errors,
                video_id=item.video_id,
                artifact=summary_json_relative,
                artifact_kind=ARTIFACT_KIND_SUMMARY,
                supported_versions=(2,),
                require_object=True,
                require_version_field=True,
            )
            if detected_summary_version is None:
                continue
            summary_version = detected_summary_version
            if summary_version == 2:
                record_evidence_ids(
                    summary_json,
                    video_id=item.video_id,
                    artifact=summary_json_relative,
                )
                try:
                    summary_json = validate_v2_summary(
                        summary_json,
                        path=summary_json_relative,
                        expected_video_id=item.video_id,
                    )
                except V2ValidationError as exc:
                    append_v2_errors(
                        exc,
                        video_id=item.video_id,
                        artifact=summary_json_relative,
                    )
                    continue

        insights = _read_json(
            insights_path,
            insights_relative,
            errors,
            video_id=item.video_id,
            artifact=insights_relative,
        )
        if insights is None:
            if not has_error(insights_relative, "invalid_json"):
                errors.append(
                    CorpusValidationIssue(
                        code="missing_metadata",
                        path=insights_relative,
                        message="JSON value is null; schema_version is unknown",
                        video_id=item.video_id,
                        artifact=insights_relative,
                        schema_version="unknown",
                    )
                )
            continue
        insights_version = _detect_artifact_version(
            insights,
            insights_relative,
            errors,
            video_id=item.video_id,
            artifact=insights_relative,
            artifact_kind=ARTIFACT_KIND_INSIGHTS,
            supported_versions=(1, 2),
            require_object=True,
        )
        if insights_version is None:
            continue

        if summary_json is not None and insights_version != 2:
            errors.append(
                CorpusValidationIssue(
                    code="bundle_schema_mismatch",
                    path=insights_relative,
                    message=(
                        "schema_version 1 contradicts sibling summary.json "
                        "schema_version 2"
                    ),
                    video_id=item.video_id,
                    artifact=insights_relative,
                    schema_version=insights_version,
                )
            )
            continue

        if insights_version == 2:
            record_evidence_ids(
                insights,
                video_id=item.video_id,
                artifact=insights_relative,
            )
            try:
                validated_v2 = validate_v2_insights(
                    insights,
                    path=insights_relative,
                    expected_video_id=item.video_id,
                )
            except V2ValidationError as exc:
                append_v2_errors(
                    exc,
                    video_id=item.video_id,
                    artifact=insights_relative,
                )
                continue
            if (
                isinstance(summary_json, Mapping)
                and summary_json.get("video_id") != validated_v2.get("video_id")
            ):
                errors.append(
                    CorpusValidationIssue(
                        code="bundle_identity_mismatch",
                        path=f"{summary_json_relative}.video_id",
                        message=(
                            f"does not agree with {insights_relative}.video_id "
                            f"{validated_v2.get('video_id')!r}"
                        ),
                        video_id=item.video_id,
                        artifact=summary_json_relative,
                        schema_version=2,
                    )
                )
                continue
            validated = validated_v2
        else:
            validated = _validate_insights(insights, errors)
            if validated is None:
                continue
        try:
            frontmatter = SourceFrontmatter.from_mapping(
                {
                    key: value
                    for key, value in parsed.metadata.items()
                    if key != "schema_version"
                }
            )
            if insights_version == 2:
                videos.append(
                    V2SourceRecord.from_mappings(
                        index=item,
                        frontmatter=frontmatter,
                        summary_markdown=parsed.markdown,
                        summary_json=summary_json if summary_version == 2 else None,
                        insights_json=validated,
                        summary_path=summary_relative,
                        insights_path=insights_relative,
                        summary_json_path=summary_json_relative if summary_version == 2 else None,
                    )
                )
            else:
                videos.append(
                    V1SourceRecord.from_mappings(
                        index=item,
                        frontmatter=frontmatter,
                        summary_markdown=parsed.markdown,
                        insights=validated,
                        summary_path=summary_relative,
                        insights_path=insights_relative,
                    )
                )
        except SourceModelError as exc:
            errors.append(f"{insights_relative}: {exc}")
    if errors:
        raise CorpusValidationError(errors)
    return LoadedCorpus(source_root, tuple(index_items), tuple(videos), tuple(warnings))
