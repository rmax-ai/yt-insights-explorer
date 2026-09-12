"""Deterministic aggregates over canonical corpus records.

The public site still has a V1-shaped export.  The consumer functions in this
module therefore have two deliberately separate paths:

* canonical ``NormalizedVideo`` records are the implementation path;
* mapping inputs are a narrow compatibility path for the E3-T4 renderer
  boundary and for callers of the V1 public contract.

The compatibility path is intentionally boring.  It must not become another
normalizer or a second source of semantic truth.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .corpus.compiler import OverlayState, ResolvedVideoState
from .corpus.normalized_models import (
    Evidence,
    NormalizedCorpus,
    NormalizedVideo,
)
from .corpus.registries import Registries, ResolvedLabel, resolve_topic
from .models import INSIGHT_TYPES
from .slug import canonical_text, concept_id, video_slug

IDEA_SECTIONS = ("article_ideas", "project_ideas", "deep_dives", "open_questions")
CLAIM_TYPE_ORDER = ("causal", "comparative", "factual", "opinion", "prediction")
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True, slots=True)
class _ConsumerContext:
    videos: tuple[Any, ...]
    canonical: bool
    overlay_state: OverlayState | None


@dataclass(frozen=True, slots=True)
class _ResolvedTopicNode:
    """One deduplicated topic node plus every source occurrence behind it."""

    id: str
    name: str
    url: str
    labels: tuple[ResolvedLabel, ...]


def _context(value: Any) -> _ConsumerContext:
    if isinstance(value, NormalizedCorpus):
        state = value.overlay_state
        return _ConsumerContext(
            videos=tuple(value.videos),
            canonical=True,
            overlay_state=state if isinstance(state, OverlayState) else None,
        )
    values = tuple(value)
    canonical = bool(values) and all(isinstance(item, NormalizedVideo) for item in values)
    legacy = bool(values) and all(isinstance(item, Mapping) for item in values)
    if values and not (canonical or legacy):
        raise TypeError("consumer inputs must contain only canonical or only compatibility records")
    return _ConsumerContext(videos=values, canonical=canonical, overlay_state=None)


def _video_id(video: Any) -> str:
    return video.video_id if isinstance(video, NormalizedVideo) else video["video_id"]


def _published_at(video: Any) -> datetime | str:
    if isinstance(video, NormalizedVideo):
        return video.source.published_at.astimezone(UTC)
    return video["source"]["published_at"]


def _published_date(video: Any) -> str:
    if isinstance(video, NormalizedVideo):
        return _published_at(video).isoformat().replace("+00:00", "Z")[:10]
    return video["source"]["published_date"]


def _published_month(video: Any) -> str:
    if isinstance(video, NormalizedVideo):
        return _published_at(video).isoformat().replace("+00:00", "Z")[:7]
    return video["source"]["published_month"]


def _video_url(video: NormalizedVideo) -> str:
    return f"videos/{video_slug(video.title, video.video_id)}/index.html"


def _sorted_videos(videos: Iterable[Any]) -> list[Any]:
    values = sorted(videos, key=_video_id)
    return sorted(values, key=_published_at, reverse=True)


def _item_sort_key(item: Any) -> tuple[Any, ...]:
    if isinstance(item, dict):
        source_index = item.get("source_index")
        return (
            0,
            source_index if isinstance(source_index, int) else 10**12,
            item.get("id", ""),
        )
    # V1 source indexes retain their published ordering.  V2 persisted IDs
    # take precedence over the adapter's positional provenance so reordering a
    # V2 array cannot change a derived collection's order.
    if item.id_kind.value == "legacy-position":
        return (0, item.source_index if item.source_index is not None else 10**12, item.id)
    return (1, item.id, item.source_index if item.source_index is not None else 10**12)


def _sorted_items(values: Iterable[Any]) -> list[Any]:
    return sorted(values, key=_item_sort_key)


def month_axis(videos: list[Any] | tuple[Any, ...]) -> list[str]:
    """Return every YYYY-MM between the first and last publication month."""

    months = [_published_month(video) for video in videos]
    if not months:
        return []
    start_year, start_month = (int(part) for part in min(months).split("-"))
    end_year, end_month = (int(part) for part in max(months).split("-"))
    result: list[str] = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        result.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            month = 1
            year += 1
    return result


def _display_item(item: dict[str, Any], video: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    result.update(
        {
            "video_id": video["video_id"],
            "video_title": video["title"],
            "video_url": video["url"],
            "channel": video["channel"],
            "source_published": video["source"]["published_at"],
        }
    )
    return result


def _legacy_tags(video: dict[str, Any]) -> list[str]:
    return [tag["name"] for tag in video["tags"]]


def _legacy_trend_labels(context: _ConsumerContext, video: Any) -> list[str]:
    if not isinstance(video, NormalizedVideo):
        return _legacy_tags(video)
    # V1's public "tags" are the compatibility projection of every retained
    # source label.  Keep the display-name selection identical to normalize.py.
    return [" ".join(label.label.split()) for label in video.labels]


def _canonical_state(
    context: _ConsumerContext,
    video: NormalizedVideo,
) -> ResolvedVideoState | None:
    if context.overlay_state is None:
        return None
    return context.overlay_state.by_video_id.get(video.video_id)


def _registries(context: _ConsumerContext) -> Registries:
    if context.overlay_state is not None:
        return context.overlay_state.registries
    return Registries()


def _concept_labels(context: _ConsumerContext, video: NormalizedVideo) -> tuple[ResolvedLabel, ...]:
    state = _canonical_state(context, video)
    if state is not None:
        return state.concepts.labels
    return _registries(context).resolve_source_record(video).labels


def _topic_labels(context: _ConsumerContext, video: NormalizedVideo) -> tuple[ResolvedLabel, ...]:
    """Return explicit normalized topic occurrences.

    V2 carries an explicit ``topics`` array.  The E1 model retains its source
    location on ``RawLabel``; using that location keeps topics and concept
    candidates in separate namespaces even though both are lossless labels.
    V1 has no topic field, so its legacy labels are deterministic unresolved
    topic candidates for the canonical path.  The compatibility projection
    below still emits the old concept-shaped tag rows.
    """

    registries = _registries(context)
    state = _canonical_state(context, video)
    if video.provenance.version == 2:
        labels = tuple(label for label in video.labels if "::topics[" in label.location)
        if labels:
            return tuple(resolve_topic(registries, label) for label in labels)
        if state is not None and state.topics:
            return state.topics
        return ()
    if video.labels:
        return tuple(resolve_topic(registries, label) for label in video.labels)
    if state is not None:
        return state.topics
    return ()


def _dedupe_topic_nodes(labels: Iterable[ResolvedLabel]) -> tuple[_ResolvedTopicNode, ...]:
    grouped: dict[str, list[ResolvedLabel]] = defaultdict(list)
    for label in labels:
        grouped[label.id].append(label)
    nodes = []
    for node_id, occurrences in grouped.items():
        ordered = sorted(
            occurrences,
            key=lambda item: (
                item.name.casefold(),
                item.name,
                item.raw_label.casefold(),
                item.raw_label,
                item.origin.value,
                item.location,
            ),
        )
        first = ordered[0]
        nodes.append(
            _ResolvedTopicNode(
                id=node_id,
                name=first.name,
                url=first.url,
                labels=tuple(ordered),
            )
        )
    return tuple(sorted(nodes, key=lambda item: (item.id, item.name.casefold(), item.name)))


def _resolution_dict(label: ResolvedLabel) -> dict[str, Any]:
    return {
        "raw_label": label.raw_label,
        "origin": label.origin.value,
        "location": label.location,
        "resolution_method": label.match_method,
        "canonical_id": label.resolved_id,
        "unresolved": label.unresolved,
    }


def _topic_rows(
    context: _ConsumerContext,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    months = month_axis(tuple(context.videos))
    monthly: dict[str, dict[str, set[str]]] = {}
    names: dict[str, str] = {}
    urls: dict[str, str] = {}
    resolutions: dict[str, list[ResolvedLabel]] = defaultdict(list)
    for video in _sorted_videos(context.videos):
        month = _published_month(video)
        if not isinstance(video, NormalizedVideo):
            continue
        for node in _dedupe_topic_nodes(_topic_labels(context, video)):
            names[node.id] = min(
                (names.get(node.id, node.name), node.name),
                key=lambda value: (value.casefold(), value),
            )
            urls[node.id] = min(urls.get(node.id, node.url), node.url)
            resolutions[node.id].extend(node.labels)
            monthly.setdefault(node.id, {}).setdefault(month, set()).add(video.video_id)

    rows = []
    for topic_id in sorted(names):
        labels = tuple(
            sorted(
                set(resolutions[topic_id]),
                key=lambda item: (
                    item.raw_label.casefold(),
                    item.raw_label,
                    item.origin.value,
                    item.location,
                ),
            )
        )
        rows.append(
            {
                "topic_id": topic_id,
                "name": names[topic_id],
                "url": urls[topic_id],
                "counts": [
                    len(monthly[topic_id].get(month, set()))
                    for month in months
                ],
                "resolutions": [_resolution_dict(label) for label in labels],
            }
        )

    active_months = months
    recent_months = active_months[-min(3, len(active_months)) :] if active_months else []
    remaining = active_months[: -len(recent_months)] if recent_months else active_months
    prior_months = remaining[-min(3, len(remaining)) :] if remaining else []
    recent_videos = sum(
        sum(_published_month(video) == month for video in context.videos)
        for month in recent_months
    )
    prior_videos = sum(
        sum(_published_month(video) == month for video in context.videos)
        for month in prior_months
    )
    rankings = []
    for topic_id in sorted(names):
        counts = monthly[topic_id]
        recent_count = sum(len(counts.get(month, set())) for month in recent_months)
        prior_count = sum(len(counts.get(month, set())) for month in prior_months)
        video_count = len({video_id for values in counts.values() for video_id in values})
        recent_rate = recent_count / max(1, recent_videos)
        prior_rate = prior_count / max(1, prior_videos)
        score = recent_rate - prior_rate
        rankings.append(
            {
                "topic_id": topic_id,
                "name": names[topic_id],
                "url": urls[topic_id],
                "video_count": video_count,
                "recent_count": recent_count,
                "prior_count": prior_count,
                "recent_rate": recent_rate,
                "prior_rate": prior_rate,
                "trend_score": score,
                "rising": bool(
                    prior_months
                    and recent_count >= 2
                    and score > 0
                    and video_count >= 2
                ),
                "resolutions": [
                    _resolution_dict(label)
                    for label in sorted(
                        set(resolutions[topic_id]),
                        key=lambda item: (
                            item.raw_label.casefold(),
                            item.raw_label,
                            item.origin.value,
                            item.location,
                        ),
                    )
                ],
            }
        )
    rankings.sort(
        key=lambda item: (
            not item["rising"],
            -item["trend_score"],
            -item["recent_count"],
            -item["video_count"],
            item["name"].casefold(),
            item["name"],
            item["topic_id"],
        )
    )
    return rows, rankings


def _legacy_tag_rows(
    context: _ConsumerContext,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reproduce the V1 tag projection from canonical source labels."""

    ordered = _sorted_videos(context.videos)
    months = month_axis(tuple(context.videos))
    names: dict[str, str] = {}
    video_months: dict[str, dict[str, set[str]]] = {}
    for video in ordered:
        month = _published_month(video)
        labels = _legacy_trend_labels(context, video)
        grouped: dict[str, str] = {}
        for label in labels:
            key = canonical_text(label)
            if not key:
                continue
            display = " ".join(label.split())
            grouped[key] = min(
                (grouped.get(key, display), display),
                key=lambda value: (value.casefold(), value),
            )
        for key, display in grouped.items():
            names[key] = min(
                (names.get(key, display), display),
                key=lambda value: (value.casefold(), value),
            )
            video_months.setdefault(key, {}).setdefault(month, set()).add(_video_id(video))

    tag_monthly = [
        {
            "concept_id": concept_id(names[key]),
            "name": names[key],
            "counts": [len(video_months[key].get(month, set())) for month in months],
        }
        for key in sorted(names, key=lambda value: (names[value].casefold(), names[value]))
    ]

    recent_months = months[-min(3, len(months)) :] if months else []
    remaining = months[: -len(recent_months)] if recent_months else months
    prior_months = remaining[-min(3, len(remaining)) :] if remaining else []
    recent_videos = sum(
        sum(_published_month(video) == month for video in context.videos)
        for month in recent_months
    )
    prior_videos = sum(
        sum(_published_month(video) == month for video in context.videos)
        for month in prior_months
    )
    rankings = []
    for key, name in names.items():
        counts = video_months[key]
        recent_count = sum(len(counts.get(month, set())) for month in recent_months)
        prior_count = sum(len(counts.get(month, set())) for month in prior_months)
        video_count = len({video_id for values in counts.values() for video_id in values})
        recent_rate = recent_count / max(1, recent_videos)
        prior_rate = prior_count / max(1, prior_videos)
        score = recent_rate - prior_rate
        rankings.append(
            {
                "concept_id": concept_id(name),
                "name": name,
                "video_count": video_count,
                "recent_count": recent_count,
                "prior_count": prior_count,
                "recent_rate": recent_rate,
                "prior_rate": prior_rate,
                "trend_score": score,
                "rising": bool(
                    prior_months
                    and recent_count >= 2
                    and score > 0
                    and video_count >= 2
                ),
            }
        )
    rankings.sort(
        key=lambda item: (
            not item["rising"],
            -item["trend_score"],
            -item["recent_count"],
            -item["video_count"],
            item["name"].casefold(),
            item["name"],
        )
    )
    return tag_monthly, rankings


def _common_trend_rows(context: _ConsumerContext) -> dict[str, Any]:
    ordered = _sorted_videos(context.videos)
    months = month_axis(tuple(context.videos))
    monthly_video_counts = {month: 0 for month in months}
    for video in ordered:
        monthly_video_counts[_published_month(video)] += 1

    cumulative = 0
    corpus_growth = []
    for month in months:
        cumulative += monthly_video_counts[month]
        corpus_growth.append(
            {"month": month, "published": monthly_video_counts[month], "cumulative": cumulative}
        )

    insight_type_monthly = []
    for month in months:
        counts = {insight_type: 0 for insight_type in INSIGHT_TYPES}
        for video in ordered:
            if _published_month(video) != month:
                continue
            insights = (
                video.core_insights
                if isinstance(video, NormalizedVideo)
                else video["core_insights"]
            )
            for insight in insights:
                insight_type = (
                    insight.type
                    if isinstance(video, NormalizedVideo)
                    else insight["type"]
                )
                counts[insight_type] += 1
        insight_type_monthly.append(
            {"month": month, "total": sum(counts.values()), "counts": counts}
        )

    idea_flow_monthly = []
    for month in months:
        values = {}
        for section in IDEA_SECTIONS:
            values[section] = sum(
                len(
                    video[section]
                    if not isinstance(video, NormalizedVideo)
                    else getattr(video, section)
                )
                for video in ordered
                if _published_month(video) == month
            )
        idea_flow_monthly.append({"month": month, **values})
    return {
        "schema_version": 1,
        "months": months,
        "corpus_growth": corpus_growth,
        "insight_type_monthly": insight_type_monthly,
        "idea_flow_monthly": idea_flow_monthly,
    }


def derive_trends(
    videos: list[Any] | tuple[Any, ...] | NormalizedCorpus,
    *,
    compatibility: bool | None = None,
) -> dict[str, Any]:
    """Calculate trend aggregates from canonical topics and publication dates.

    ``compatibility=True`` returns the exact V1 export shape.  Mapping inputs
    always use that path.  Canonical callers get explicit ``topic_id`` rows by
    default and may request the legacy projection when feeding an older
    renderer or export.
    """

    context = _context(videos)
    if not context.canonical:
        compatibility = True
    elif compatibility is None:
        compatibility = all(video.provenance.version == 1 for video in context.videos)
    common = _common_trend_rows(context)
    if context.canonical:
        topic_monthly, topic_rankings = _topic_rows(context)
        tag_monthly, tag_rankings = _legacy_tag_rows(context)
        if compatibility:
            return {
                **common,
                "tag_monthly": tag_monthly,
                "tag_rankings": tag_rankings,
            }
        return {
            **common,
            "topic_monthly": topic_monthly,
            "topic_rankings": topic_rankings,
            # Keep a named compatibility projection available to callers that
            # still serialize the V1 contract.  It is not used for canonical
            # topic aggregation.
            "tag_monthly": tag_monthly,
            "tag_rankings": tag_rankings,
        }

    tag_monthly, tag_rankings = _legacy_tag_rows(context)
    return {
        **common,
        "tag_monthly": tag_monthly,
        "tag_rankings": tag_rankings,
    }


def _canonical_evidence(value: Evidence) -> dict[str, Any]:
    occurrence = value.occurrence
    return {
        "id": value.content_id,
        "text": value.text,
        "availability": value.availability.value,
        "timestamp_seconds": value.timestamp_seconds,
        "source_url": value.source_url,
        "resolution_method": value.resolution_method,
        "content_id": value.content_id,
        "occurrence": (
            {
                "section": occurrence.section,
                "item_index": occurrence.item_index,
                "quote_index": occurrence.quote_index,
            }
            if occurrence is not None
            else None
        ),
        "timestamp_method": value.timestamp_method,
        "provenance": {
            "source_version": value.provenance.version,
            "location": value.provenance.location,
            "source_index": value.provenance.source_index,
        },
    }


def _canonical_item(item: Any, section: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": item.id,
        "occurrence_id": item.id,
        "id_kind": item.id_kind.value,
        "source_index": item.source_index,
        "fingerprint": item.fingerprint.value,
    }
    if section == "core_insights":
        result.update(
            {
                "insight": item.insight,
                "type": item.type,
                "why_it_matters": item.why_it_matters,
                "generalization": item.generalization,
                "evidence": [_canonical_evidence(value) for value in item.evidence],
                "evidence_strength": item.evidence_strength,
                "novelty": item.novelty,
            }
        )
    elif section == "deep_dives":
        result.update(
            {
                "topic": item.topic,
                "research_question": item.research_question,
                "why": item.why,
                "trigger_insight": item.trigger_insight,
                "evidence": [_canonical_evidence(value) for value in item.evidence],
                "priority": item.priority,
            }
        )
    elif section == "article_ideas":
        result.update(
            {
                "title": item.title,
                "thesis": item.thesis,
                "angle": item.angle,
                "based_on": item.based_on,
                "audience": item.audience,
            }
        )
    elif section == "project_ideas":
        result.update(
            {
                "name": item.name,
                "hypothesis": item.hypothesis,
                "poc": item.poc,
                "measurement": item.measurement,
                "based_on": item.based_on,
                "raw_fit": item.raw_fit,
            }
        )
    elif section == "architectural_implications":
        result.update(
            {
                "observation": item.observation,
                "before": item.before,
                "after": item.after,
                "consequence": item.consequence,
            }
        )
    elif section == "tradeoffs_and_failure_modes":
        result.update(
            {
                "topic": item.topic,
                "benefit": item.benefit,
                "cost_or_risk": item.cost_or_risk,
                "evidence": _canonical_evidence(item.evidence),
            }
        )
    elif section == "open_questions":
        result.update(
            {
                "question": item.question,
                "why_unresolved": item.why_unresolved,
                "research_direction": item.research_direction,
            }
        )
    elif section == "key_claims":
        result.update(
            {
                "claim": item.claim,
                "claim_type": item.claim_type,
                "evidence": _canonical_evidence(item.evidence),
                "verification_requested": item.verification_requested,
                "verification_needed": item.verification_requested,
                "verification_question": item.verification_question,
            }
        )
    elif section == "connections":
        result.update(
            {
                "concept": item.concept,
                "connects_to": item.connects_to,
                "relationship": item.relationship,
            }
        )
    else:
        raise ValueError(f"unknown insight section: {section}")
    return result


def _compat_item(item: Any, section: str) -> dict[str, Any]:
    """Project one canonical item to the unchanged V1 item shape."""

    result: dict[str, Any] = {"id": item.id, "source_index": item.source_index}
    if section in {"core_insights", "deep_dives"}:
        result["evidence_quotes"] = [
            {
                "text": evidence.text,
                "timestamp_seconds": evidence.timestamp_seconds,
                "source_url": evidence.source_url,
            }
            for evidence in item.evidence
        ]
        if section == "core_insights":
            result.update(
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
            result.update(
                {
                    "topic": item.topic,
                    "research_question": item.research_question,
                    "why": item.why,
                    "trigger_insight": item.trigger_insight,
                    "priority": item.priority,
                }
            )
    elif section == "tradeoffs_and_failure_modes":
        evidence = item.evidence
        result.update(
            {
                "topic": item.topic,
                "benefit": item.benefit,
                "cost_or_risk": item.cost_or_risk,
                "evidence_quote": {
                    "text": evidence.text,
                    "timestamp_seconds": evidence.timestamp_seconds,
                    "source_url": evidence.source_url,
                },
            }
        )
    elif section == "key_claims":
        result.update(
            {
                "claim": item.claim,
                "claim_type": item.claim_type,
                "evidence": item.evidence.text,
                "verification_needed": item.verification_requested,
                "verification_question": item.verification_question,
                "verification_status": (
                    "needed" if item.verification_requested else "not-needed"
                ),
            }
        )
    elif section == "article_ideas":
        result.update(
            {
                "title": item.title,
                "thesis": item.thesis,
                "angle": item.angle,
                "based_on": item.based_on,
                "audience": item.audience,
            }
        )
    elif section == "project_ideas":
        result.update(
            {
                "name": item.name,
                "hypothesis": item.hypothesis,
                "poc": item.poc,
                "measurement": item.measurement,
                "based_on": item.based_on,
                "fits": item.raw_fit,
            }
        )
    elif section == "architectural_implications":
        result.update(
            {
                "observation": item.observation,
                "before": item.before,
                "after": item.after,
                "consequence": item.consequence,
            }
        )
    elif section == "open_questions":
        result.update(
            {
                "question": item.question,
                "why_unresolved": item.why_unresolved,
                "research_direction": item.research_direction,
            }
        )
    elif section == "connections":
        result.update(
            {
                "concept": item.concept,
                "connects_to": item.connects_to,
                "relationship": item.relationship,
            }
        )
    else:
        raise ValueError(f"unknown insight section: {section}")
    return result


def _canonical_item_with_video(item: Any, section: str, video: NormalizedVideo) -> dict[str, Any]:
    result = _canonical_item(item, section)
    result.update(
        {
            "video_id": video.video_id,
            "video_title": video.title,
            "video_url": _video_url(video),
            "channel": video.channel,
            "source_published": _published_at(video).isoformat().replace("+00:00", "Z"),
        }
    )
    return result


def _compat_item_with_video(item: Any, section: str, video: NormalizedVideo) -> dict[str, Any]:
    result = _compat_item(item, section)
    result.update(
        {
            "video_id": video.video_id,
            "video_title": video.title,
            "video_url": _video_url(video),
            "channel": video.channel,
            "source_published": _published_at(video).isoformat().replace("+00:00", "Z"),
        }
    )
    return result


def derive_ideas(
    videos: list[Any] | tuple[Any, ...] | NormalizedCorpus,
    *,
    compatibility: bool | None = None,
) -> dict[str, Any]:
    """Aggregate idea sections using canonical item identities."""

    context = _context(videos)
    if not context.canonical:
        compatibility = True
    elif compatibility is None:
        compatibility = all(video.provenance.version == 1 for video in context.videos)
    if context.canonical:
        result: dict[str, Any] = {"schema_version": 1}
        for section in IDEA_SECTIONS:
            items = [
                (
                    _compat_item_with_video(item, section, video)
                    if compatibility
                    else _canonical_item_with_video(item, section, video)
                )
                for video in _sorted_videos(context.videos)
                for item in _sorted_items(getattr(video, section))
            ]
            if section == "deep_dives":
                items.sort(
                    key=lambda item: (
                        PRIORITY_ORDER[item["priority"]],
                        -int(item["source_published"][:10].replace("-", "")),
                        item["video_id"],
                        item.get("source_index", 10**12),
                        item.get("occurrence_id", item.get("id", "")),
                    )
                )
            result[section] = items
        return result

    result = {"schema_version": 1}
    for section in IDEA_SECTIONS:
        items = [
            _display_item(item, video)
            for video in _sorted_videos(context.videos)
            for item in _sorted_items(video[section])
        ]
        if section == "deep_dives":
            items.sort(
                key=lambda item: (
                    PRIORITY_ORDER[item["priority"]],
                    -int(item["source_published"][:10].replace("-", "")),
                    item["video_id"],
                    item.get("source_index", 10**12),
                    item.get("id", ""),
                )
            )
        result[section] = items
    return result


def _claim_resolution_map(
    context: _ConsumerContext,
    video: NormalizedVideo,
) -> dict[str, Any]:
    state = _canonical_state(context, video)
    if state is None:
        return {}
    return {resolution.claim.id: resolution for resolution in state.claims}


def derive_claims(
    videos: list[Any] | tuple[Any, ...] | NormalizedCorpus,
    *,
    compatibility: bool | None = None,
) -> dict[str, Any]:
    """Aggregate claims while preserving request and review state separately."""

    context = _context(videos)
    if not context.canonical:
        compatibility = True
    elif compatibility is None:
        compatibility = all(video.provenance.version == 1 for video in context.videos)
    if context.canonical:
        claims = []
        for video in _sorted_videos(context.videos):
            resolutions = _claim_resolution_map(context, video)
            for claim in _sorted_items(video.key_claims):
                if compatibility:
                    projected = _compat_item_with_video(claim, "key_claims", video)
                else:
                    projected = _canonical_item_with_video(claim, "key_claims", video)
                    resolution = resolutions.get(claim.id)
                    review_status = resolution.status if resolution is not None else None
                    projected["review_status"] = review_status
                    projected["ledger_review_state"] = review_status
                    projected["claim_review_occurrence_id"] = (
                        resolution.occurrence_id if resolution is not None else None
                    )
                claims.append(projected)
        claims.sort(
            key=lambda claim: (
                0 if (
                    claim.get("verification_requested", claim.get("verification_needed", False))
                ) else 1,
                CLAIM_TYPE_ORDER.index(claim["claim_type"])
                if claim["claim_type"] in CLAIM_TYPE_ORDER
                else len(CLAIM_TYPE_ORDER),
                claim["source_published"],
                claim["video_id"],
                claim.get("source_index", 10**12),
                claim.get("occurrence_id", claim.get("id", "")),
            )
        )
        return {"schema_version": 1, "claims": claims}

    claims = [
        _display_item(claim, video)
        for video in _sorted_videos(context.videos)
        for claim in _sorted_items(video["key_claims"])
    ]
    claims.sort(
        key=lambda claim: (
            0 if claim["verification_status"] == "needed" else 1,
            CLAIM_TYPE_ORDER.index(claim["claim_type"]),
            claim["source_published"],
            claim["video_id"],
            claim.get("source_index", 10**12),
            claim.get("id", ""),
        )
    )
    return {"schema_version": 1, "claims": claims}


def format_month_range(months: list[str]) -> str:
    if not months:
        return "No publication dates"
    return f"{months[0]} to {months[-1]}"
