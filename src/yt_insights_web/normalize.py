"""Turn validated source artifacts into the stable public data contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .frontmatter import render_markdown
from .load import LoadedCorpus
from .models import INSIGHT_SECTIONS
from .slug import canonical_text, concept_id, concept_slug, item_id, video_slug


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


def _canonical_tag_names(raw_video: Any) -> list[str]:
    values = list(raw_video.frontmatter.get("tags") or []) + list(
        raw_video.insights.get("tags") or []
    )
    grouped: dict[str, list[str]] = {}
    for value in values:
        display = " ".join(value.split())
        if display:
            grouped.setdefault(canonical_text(display), []).append(display)
    return [min(values, key=lambda value: (value.casefold(), value)) for values in grouped.values()]


def _item(section: str, index: int, video_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    result = dict(raw)
    result["id"] = item_id(video_id, section, index)
    result["source_index"] = index
    return result


def _normalize_section(
    section: str, values: list[dict[str, Any]], video_id: str
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(values):
        result = _item(section, index, video_id, raw)
        if section in {"core_insights", "deep_dives"}:
            result["evidence_quotes"] = [_quote(quote) for quote in raw["evidence_quotes"]]
        elif section == "tradeoffs_and_failure_modes":
            result["evidence_quote"] = _quote(raw["evidence_quote"])
        elif section == "key_claims":
            result["verification_status"] = "needed" if raw["verification_needed"] else "not-needed"
        normalized.append(result)
    return normalized


def normalize_corpus(corpus: LoadedCorpus) -> NormalizedCorpus:
    """Return normalized dictionaries with no source checkout paths."""

    concept_display: dict[str, str] = {}
    tag_video_ids: dict[str, set[str]] = {}
    connection_video_ids: dict[str, set[str]] = {}
    video_tag_keys: dict[str, list[str]] = {}
    normalized_videos: list[dict[str, Any]] = []
    index_items = tuple(
        {
            "video_id": item.video_id,
            "title": item.title,
            "channel": item.channel,
            "status": item.status,
            "ingested_at": _iso(item.ingested_at),
            "cost_usd_total": item.cost_usd_total,
        }
        for item in corpus.index_items
    )

    for raw_video in corpus.videos:
        frontmatter = raw_video.frontmatter
        published = frontmatter["source_published"]
        tags = []
        for name in _canonical_tag_names(raw_video):
            key = canonical_text(name)
            concept_display[key] = min(
                (concept_display.get(key, name), name),
                key=lambda value: (value.casefold(), value),
            )
            tag_video_ids.setdefault(key, set()).add(raw_video.video_id)
            tags.append(key)
        video_tag_keys[raw_video.video_id] = tags

        for connection in raw_video.insights["connections"]:
            for endpoint in (connection["concept"], connection["connects_to"]):
                key = canonical_text(endpoint)
                concept_display[key] = min(
                    (concept_display.get(key, endpoint), endpoint),
                    key=lambda value: (value.casefold(), value),
                )
                connection_video_ids.setdefault(key, set()).add(raw_video.video_id)
        slug = video_slug(raw_video.title, raw_video.video_id)
        video = {
            "schema_version": 1,
            "video_id": raw_video.video_id,
            "slug": slug,
            "url": f"videos/{slug}/index.html",
            "title": raw_video.title,
            "channel": raw_video.channel,
            "status": raw_video.index.status,
            "ingested_at": _iso(raw_video.index.ingested_at),
            "cost_usd_total": raw_video.index.cost_usd_total,
            "source": {
                "type": frontmatter["source_type"],
                "uri": frontmatter["source_uri"],
                "title": frontmatter["source_title"],
                "author": frontmatter["source_author"],
                "published_at": _iso(published),
                "published_date": _date(published),
                "published_month": _month(published),
            },
            "document": {
                "type": frontmatter["type"],
                "description": frontmatter["description"],
                "urn": frontmatter["id"],
                "status": frontmatter["status"],
                "confidence": frontmatter["confidence"],
                "visibility": frontmatter["visibility"],
                "captured_at": _iso(frontmatter["captured_at"]),
                "generated_by": frontmatter["generated_by"],
                "review_status": frontmatter["review_status"],
            },
            "summary": {
                "markdown": raw_video.summary_markdown,
                "html": render_markdown(raw_video.summary_markdown),
            },
            "tags": [],
        }
        for section in INSIGHT_SECTIONS:
            video[section] = _normalize_section(
                section, raw_video.insights[section], raw_video.video_id
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
