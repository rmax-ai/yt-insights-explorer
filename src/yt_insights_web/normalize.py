"""Turn validated source artifacts into the stable public data contract.

``normalize_corpus()`` is the compatibility façade and the documented public
compiler entry point.  It invokes the pure ``corpus.compiler`` first, then
projects the typed result back to the dictionary contract consumed by the
existing derive and render modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .corpus import compiler
from .frontmatter import render_markdown
from .load import LoadedCorpus
from .models import INSIGHT_SECTIONS
from .slug import canonical_text, concept_id, concept_slug, video_slug


@dataclass(frozen=True)
class NormalizedCorpus:
    videos: tuple[dict[str, Any], ...]
    concepts: tuple[dict[str, Any], ...]
    index_items: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]


def _iso(value: datetime | str) -> str:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _date(value: datetime | str) -> str:
    return _iso(value)[:10]


def _month(value: datetime | str) -> str:
    return _iso(value)[:7]


def _text(value: Any) -> str:
    return " ".join(str(value).split())


def _source_url(uri: str | None, timestamp: Any = None) -> str | None:
    if uri is None:
        return None
    if timestamp is None:
        return uri
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        return f"{uri}&t={int(timestamp)}s" if "?" in uri else f"{uri}?t={int(timestamp)}s"
    return uri


def _quote(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {"text": value, "timestamp_seconds": None, "source_url": None}
    timestamp = value.get("timestamp_seconds")
    source_url = value.get("source_url")
    return {
        "text": _text(value.get("text", "")),
        "timestamp_seconds": timestamp,
        "source_url": _source_url(source_url, timestamp),
    }


def _canonical_tag_names(compiled_video: Any) -> list[str]:
    values = [label.label for label in compiled_video.labels]
    grouped: dict[str, list[str]] = {}
    for value in values:
        display = " ".join(value.split())
        if display:
            grouped.setdefault(canonical_text(display), []).append(display)
    return [min(values, key=lambda value: (value.casefold(), value)) for values in grouped.values()]


def _project_evidence(value: Any, legacy_value: Any = None) -> dict[str, Any]:
    if legacy_value is not None:
        return _quote(legacy_value)
    return {
        "text": value.text,
        "timestamp_seconds": value.timestamp_seconds,
        "source_url": value.source_url,
    }


def _project_item(item: Any, values: dict[str, Any]) -> dict[str, Any]:
    result = dict(values)
    result["id"] = item.id
    result["source_index"] = item.source_index
    return result


def _project_section(
    section: str, values: tuple[Any, ...], legacy_values: tuple[Any, ...]
) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for item, legacy_item in zip(values, legacy_values, strict=True):
        if section in {"core_insights", "deep_dives"}:
            fields = {
                "evidence_quotes": [
                    _project_evidence(quote, legacy_quote)
                    for quote, legacy_quote in zip(
                        item.evidence,
                        legacy_item.evidence_quotes,
                        strict=True,
                    )
                ],
            }
            if section == "core_insights":
                fields.update(
                    {
                        "insight": item.insight,
                        "type": item.type,
                        "why_it_matters": item.why_it_matters,
                        "generalization": item.generalization,
                        "evidence_strength": item.evidence_strength,
                        "novelty": item.novelty,
                    }
                )
            else:
                fields.update(
                    {
                        "topic": item.topic,
                        "research_question": item.research_question,
                        "why": item.why,
                        "trigger_insight": item.trigger_insight,
                        "priority": item.priority,
                    }
                )
        elif section == "tradeoffs_and_failure_modes":
            fields = {
                "topic": item.topic,
                "benefit": item.benefit,
                "cost_or_risk": item.cost_or_risk,
                "evidence_quote": _project_evidence(item.evidence, legacy_item.evidence_quote),
            }
        elif section == "key_claims":
            fields = {
                "claim": item.claim,
                "claim_type": item.claim_type,
                "evidence": _project_evidence(item.evidence, legacy_item.evidence)["text"],
                "verification_needed": item.verification_requested,
                "verification_question": item.verification_question,
                "verification_status": (
                    "needed" if item.verification_requested else "not-needed"
                ),
            }
        elif section == "article_ideas":
            fields = {
                "title": item.title,
                "thesis": item.thesis,
                "angle": item.angle,
                "based_on": item.based_on,
                "audience": item.audience,
            }
        elif section == "project_ideas":
            fields = {
                "name": item.name,
                "hypothesis": item.hypothesis,
                "poc": item.poc,
                "measurement": item.measurement,
                "based_on": item.based_on,
                "fits": item.raw_fit,
            }
        elif section == "architectural_implications":
            fields = {
                "observation": item.observation,
                "before": item.before,
                "after": item.after,
                "consequence": item.consequence,
            }
        elif section == "open_questions":
            fields = {
                "question": item.question,
                "why_unresolved": item.why_unresolved,
                "research_direction": item.research_direction,
            }
        elif section == "connections":
            fields = {
                "concept": item.concept,
                "connects_to": item.connects_to,
                "relationship": item.relationship,
            }
        else:
            raise ValueError(f"unknown insight section: {section}")
        projected.append(_project_item(item, fields))
    return projected


def normalize_corpus(corpus: LoadedCorpus) -> NormalizedCorpus:
    """Compile V1 records and return the unchanged public dictionary contract."""

    compiled = compiler.compile_corpus(corpus.videos)
    compiled_index_items = {item.video_id: item for item in compiled.index_items}
    raw_videos = {video.video_id: video for video in corpus.videos}
    concept_display: dict[str, str] = {}
    tag_video_ids: dict[str, set[str]] = {}
    connection_video_ids: dict[str, set[str]] = {}
    video_tag_keys: dict[str, list[str]] = {}
    normalized_videos: list[dict[str, Any]] = []
    index_items = tuple(
        {
            "video_id": compiled_item.video_id if compiled_item else item.video_id,
            "title": compiled_item.title if compiled_item else item.title,
            "channel": compiled_item.channel if compiled_item else item.channel,
            "status": compiled_item.status if compiled_item else item.status,
            "ingested_at": _iso(compiled_item.ingested_at if compiled_item else item.ingested_at),
            "cost_usd_total": (
                compiled_item.cost_usd_total if compiled_item else item.cost_usd_total
            ),
        }
        for item in corpus.index_items
        for compiled_item in (compiled_index_items.get(item.video_id),)
    )

    for compiled_video in compiled.videos:
        tags = []
        for name in _canonical_tag_names(compiled_video):
            key = canonical_text(name)
            concept_display[key] = min(
                (concept_display.get(key, name), name),
                key=lambda value: (value.casefold(), value),
            )
            tag_video_ids.setdefault(key, set()).add(compiled_video.video_id)
            tags.append(key)
        video_tag_keys[compiled_video.video_id] = tags

        for connection in compiled_video.connections:
            for endpoint in (connection.concept, connection.connects_to):
                key = canonical_text(endpoint)
                concept_display[key] = min(
                    (concept_display.get(key, endpoint), endpoint),
                    key=lambda value: (value.casefold(), value),
                )
                connection_video_ids.setdefault(key, set()).add(compiled_video.video_id)
        source = compiled_video.source
        document = compiled_video.document
        summary = compiled_video.summary
        slug = video_slug(compiled_video.title, compiled_video.video_id)
        video = {
            "schema_version": 1,
            "video_id": compiled_video.video_id,
            "slug": slug,
            "url": f"videos/{slug}/index.html",
            "title": compiled_video.title,
            "channel": compiled_video.channel,
            "status": compiled_video.status,
            "ingested_at": _iso(compiled_video.ingested_at),
            "cost_usd_total": compiled_index_items[compiled_video.video_id].cost_usd_total,
            "source": {
                "type": source.source_type,
                "uri": source.uri,
                "title": source.title,
                "author": source.author,
                "published_at": _iso(source.published_at),
                "published_date": _date(source.published_at),
                "published_month": _month(source.published_at),
            },
            "document": {
                "type": document.type,
                "description": document.description,
                "urn": document.urn,
                "status": document.status,
                "confidence": document.confidence,
                "visibility": document.visibility,
                "captured_at": _iso(document.captured_at),
                "generated_by": document.generated_by,
                "review_status": document.review_status,
            },
            "summary": {
                "markdown": summary.markdown,
                "html": render_markdown(summary.markdown),
            },
            "tags": [],
        }
        raw_video = raw_videos[compiled_video.video_id]
        for section in INSIGHT_SECTIONS:
            video[section] = _project_section(
                section,
                getattr(compiled_video, section),
                getattr(raw_video.insights, section),
            )
        normalized_videos.append(video)

    all_concept_keys = sorted(concept_display)
    concept_rows = []
    for key in all_concept_keys:
        name = concept_display[key]
        concept_rows.append(
            {
                "id": concept_id(name),
                "name": name,
                "slug": concept_slug(name),
                "url": f"concepts/{concept_slug(name)}/index.html",
                "video_ids": sorted(
                    tag_video_ids.get(key, set()) | connection_video_ids.get(key, set())
                ),
                "tag_video_count": len(tag_video_ids.get(key, set())),
                "connection_video_count": len(connection_video_ids.get(key, set())),
                "degree": 0,
                "x": 0,
                "y": 0,
            }
        )
    concept_by_key = {canonical_text(row["name"]): row for row in concept_rows}
    for video in normalized_videos:
        video["tags"] = [
            {
                "name": concept_display[key],
                "concept_id": concept_by_key[key]["id"],
                "url": concept_by_key[key]["url"],
            }
            for key in video_tag_keys[video["video_id"]]
        ]
        for connection in video["connections"]:
            source_key = canonical_text(connection["concept"])
            target_key = canonical_text(connection["connects_to"])
            connection["concept_id"] = concept_by_key[source_key]["id"]
            connection["connects_to_id"] = concept_by_key[target_key]["id"]

    normalized_videos.sort(key=lambda video: video["video_id"])
    normalized_videos.sort(
        key=lambda video: video["source"]["published_at"],
        reverse=True,
    )
    return NormalizedCorpus(
        videos=tuple(normalized_videos),
        concepts=tuple(sorted(concept_rows, key=lambda row: (row["slug"], row["name"]))),
        index_items=index_items,
        warnings=corpus.warnings,
    )
