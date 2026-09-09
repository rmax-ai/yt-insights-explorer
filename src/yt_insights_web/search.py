"""Build and rank the embedded, file://-safe search index."""

from __future__ import annotations

import re
from typing import Any

KIND_ORDER = {
    "video": 0,
    "concept": 1,
    "insight": 2,
    "article": 3,
    "project": 4,
    "deep-dive": 5,
    "question": 6,
    "claim": 7,
}
SECTION_KINDS = {
    "article_ideas": "article",
    "project_ideas": "project",
    "deep_dives": "deep-dive",
    "open_questions": "question",
    "key_claims": "claim",
}


def _truncate(text: str) -> str:
    return text[:2000]


def _joined(*values: Any) -> str:
    return " ".join(str(value) for value in values if value not in (None, ""))


def _relative_root_url(url: str) -> str:
    return f"../{url}"


def _video_record(video: dict[str, Any]) -> dict[str, Any]:
    tags = [tag["name"] for tag in video["tags"]]
    summary = video.get("summary", {})
    document = video.get("document", {})
    return {
        "id": video["video_id"],
        "kind": "video",
        "title": video["title"],
        "text": _truncate(_joined(summary.get("markdown"), document.get("description"))),
        "tags": tags,
        "channel": video["channel"],
        "published_date": video["source"]["published_date"],
        "url": _relative_root_url(video["url"]),
    }


def build_search_records(
    videos: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    concepts: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for video in videos:
        records.append(_video_record(video))
        tags = [tag["name"] for tag in video["tags"]]
        for insight in video["core_insights"]:
            records.append(
                {
                    "id": insight["id"],
                    "kind": "insight",
                    "title": insight["insight"],
                    "text": _truncate(
                        _joined(
                            insight["insight"],
                            insight["why_it_matters"],
                            insight["generalization"],
                            " ".join(quote["text"] for quote in insight["evidence_quotes"]),
                        )
                    ),
                    "tags": tags,
                    "channel": video["channel"],
                    "published_date": video["source"]["published_date"],
                    "url": _relative_root_url(f"{video['url']}#{insight['id']}"),
                }
            )
        for section, kind in SECTION_KINDS.items():
            for item in video[section]:
                title = (
                    item.get("title")
                    or item.get("name")
                    or item.get("topic")
                    or item.get("question")
                    or item.get("claim")
                )
                text = _truncate(
                    _joined(
                        *(
                            value
                            for key, value in item.items()
                            if key not in {"id", "source_index", "video_id", "video_url"}
                            and isinstance(value, (str, int, float, bool))
                        )
                    )
                )
                records.append(
                    {
                        "id": item["id"],
                        "kind": kind,
                        "title": title,
                        "text": text,
                        "tags": tags,
                        "channel": video["channel"],
                        "published_date": video["source"]["published_date"],
                        "url": _relative_root_url(f"{video['url']}#{item['id']}"),
                    }
                )
    for concept in concepts:
        records.append(
            {
                "id": concept["id"],
                "kind": "concept",
                "title": concept["name"],
                "text": concept["name"],
                "tags": [concept["name"]],
                "channel": "",
                "published_date": None,
                "url": _relative_root_url(concept["url"]),
            }
        )
    return sorted(
        records,
        key=lambda record: (
            KIND_ORDER[record["kind"]],
            record["title"].casefold(),
            record.get("video_id", ""),
            record["id"],
        ),
    )


def _tokens(value: str) -> list[str]:
    return [token for token in re.split(r"\W+", value.casefold(), flags=re.UNICODE) if token]


def rank_search_records(query: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    query_tokens = _tokens(query)
    if not query_tokens:
        return []
    query_casefolded = query.casefold().strip()
    matches = []
    for record in records:
        title = record["title"]
        haystack = _joined(
            title, record["text"], " ".join(record["tags"]), record["channel"]
        ).casefold()
        if not all(token in haystack for token in query_tokens):
            continue
        title_tokens = _tokens(title)
        occurrences = sum(haystack.count(token) for token in query_tokens)
        matches.append(
            (
                title.casefold() == query_casefolded,
                title.casefold().startswith(query_casefolded),
                all(token in title_tokens for token in query_tokens),
                occurrences,
                record,
            )
        )
    matches.sort(
        key=lambda match: (
            not match[0],
            not match[1],
            not match[2],
            -match[3],
            KIND_ORDER[match[4]["kind"]],
            match[4]["title"].casefold(),
            match[4]["id"],
        )
    )
    return [match[-1] for match in matches]
