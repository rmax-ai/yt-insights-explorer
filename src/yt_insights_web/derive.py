"""Deterministic aggregate data for the static pages."""

from __future__ import annotations

from typing import Any

from .models import INSIGHT_TYPES

IDEA_SECTIONS = ("article_ideas", "project_ideas", "deep_dives", "open_questions")
CLAIM_TYPE_ORDER = ("causal", "comparative", "factual", "opinion", "prediction")
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def month_axis(videos: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[str]:
    """Return every YYYY-MM between the first and last publication month."""

    months = [video["source"]["published_month"] for video in videos]
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


def _sorted_videos(
    videos: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    result = sorted(videos, key=lambda video: video["video_id"])
    return sorted(result, key=lambda video: video["source"]["published_at"], reverse=True)


def derive_trends(videos: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> dict[str, Any]:
    """Calculate all trend aggregates from source publication months."""

    ordered = _sorted_videos(videos)
    months = month_axis(ordered)
    monthly_video_counts = {month: 0 for month in months}
    for video in ordered:
        monthly_video_counts[video["source"]["published_month"]] += 1

    cumulative = 0
    corpus_growth = []
    for month in months:
        cumulative += monthly_video_counts[month]
        corpus_growth.append(
            {"month": month, "published": monthly_video_counts[month], "cumulative": cumulative}
        )

    tag_names: dict[str, str] = {}
    tag_video_months: dict[str, dict[str, set[str]]] = {}
    for video in ordered:
        month = video["source"]["published_month"]
        for tag in video["tags"]:
            key = tag["concept_id"]
            tag_names[key] = tag["name"]
            tag_video_months.setdefault(key, {}).setdefault(month, set()).add(video["video_id"])
    tag_monthly = []
    for concept_id in sorted(
        tag_names, key=lambda key: (tag_names[key].casefold(), tag_names[key])
    ):
        counts = [len(tag_video_months[concept_id].get(month, set())) for month in months]
        tag_monthly.append(
            {
                "concept_id": concept_id,
                "name": tag_names[concept_id],
                "counts": counts,
            }
        )

    active_months = months
    recent_months = active_months[-min(3, len(active_months)) :] if active_months else []
    remaining = active_months[: -len(recent_months)] if recent_months else active_months
    prior_months = remaining[-min(3, len(remaining)) :] if remaining else []
    recent_videos = sum(monthly_video_counts[month] for month in recent_months)
    prior_videos = sum(monthly_video_counts[month] for month in prior_months)
    rankings = []
    for concept_id, name in tag_names.items():
        counts = tag_video_months[concept_id]
        recent_count = sum(len(counts.get(month, set())) for month in recent_months)
        prior_count = sum(len(counts.get(month, set())) for month in prior_months)
        video_count = len({video_id for values in counts.values() for video_id in values})
        recent_rate = recent_count / max(1, recent_videos)
        prior_rate = prior_count / max(1, prior_videos)
        score = recent_rate - prior_rate
        has_prior = bool(prior_months)
        rankings.append(
            {
                "concept_id": concept_id,
                "name": name,
                "video_count": video_count,
                "recent_count": recent_count,
                "prior_count": prior_count,
                "recent_rate": recent_rate,
                "prior_rate": prior_rate,
                "trend_score": score,
                "rising": bool(has_prior and recent_count >= 2 and score > 0 and video_count >= 2),
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

    insight_type_monthly = []
    for month in months:
        counts = {insight_type: 0 for insight_type in INSIGHT_TYPES}
        for video in ordered:
            if video["source"]["published_month"] == month:
                for insight in video["core_insights"]:
                    counts[insight["type"]] += 1
        insight_type_monthly.append(
            {"month": month, "total": sum(counts.values()), "counts": counts}
        )

    idea_flow_monthly = []
    for month in months:
        values = {
            section: sum(
                len(video[section])
                for video in ordered
                if video["source"]["published_month"] == month
            )
            for section in IDEA_SECTIONS
        }
        idea_flow_monthly.append({"month": month, **values})

    return {
        "schema_version": 1,
        "months": months,
        "corpus_growth": corpus_growth,
        "tag_monthly": tag_monthly,
        "tag_rankings": rankings,
        "insight_type_monthly": insight_type_monthly,
        "idea_flow_monthly": idea_flow_monthly,
    }


def derive_ideas(videos: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> dict[str, Any]:
    result = {"schema_version": 1}
    for section in IDEA_SECTIONS:
        items = [
            _display_item(item, video)
            for video in _sorted_videos(videos)
            for item in video[section]
        ]
        if section == "deep_dives":
            items.sort(
                key=lambda item: (
                    PRIORITY_ORDER[item["priority"]],
                    -int(item["source_published"][:10].replace("-", "")),
                    item["video_id"],
                    item["source_index"],
                )
            )
        result[section] = items
    return result


def derive_claims(videos: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> dict[str, Any]:
    claims = [
        _display_item(claim, video)
        for video in _sorted_videos(videos)
        for claim in video["key_claims"]
    ]
    claims.sort(
        key=lambda claim: (
            0 if claim["verification_status"] == "needed" else 1,
            CLAIM_TYPE_ORDER.index(claim["claim_type"]),
            claim["source_published"],
            claim["video_id"],
            claim["source_index"],
        )
    )
    return {"schema_version": 1, "claims": claims}


def format_month_range(months: list[str]) -> str:
    if not months:
        return "No publication dates"
    return f"{months[0]} to {months[-1]}"
