from __future__ import annotations

from yt_insights_web.search import build_search_records, rank_search_records


def test_search_records_are_bounded_and_sorted() -> None:
    videos = [
        {
            "video_id": "b",
            "title": "Beta",
            "channel": "Channel",
            "url": "videos/b/index.html",
            "source": {"published_date": "2026-01-01"},
            "tags": [{"name": "agents", "concept_id": "c", "url": "concepts/c/index.html"}],
            "core_insights": [],
            "article_ideas": [],
            "project_ideas": [],
            "deep_dives": [],
            "open_questions": [],
            "key_claims": [],
            "connections": [],
        },
        {
            "video_id": "a",
            "title": "Alpha",
            "channel": "Channel",
            "url": "videos/a/index.html",
            "source": {"published_date": None},
            "tags": [],
            "core_insights": [
                {
                    "id": "a:core_insights:0",
                    "source_index": 0,
                    "insight": "An insight " + ("x" * 3000),
                    "type": "architecture",
                    "why_it_matters": "why",
                    "generalization": "general",
                    "evidence_quotes": [],
                }
            ],
            "article_ideas": [],
            "project_ideas": [],
            "deep_dives": [],
            "open_questions": [],
            "key_claims": [],
            "connections": [],
        },
    ]
    concepts = [{"id": "c", "name": "Agents", "url": "concepts/c/index.html"}]

    records = build_search_records(videos, concepts)
    insight = next(record for record in records if record["kind"] == "insight")

    assert len(insight["text"]) == 2000
    assert insight["url"].startswith("../videos/")
    assert [record["kind"] for record in records[:2]] == ["video", "video"]


def test_search_ranking_is_token_based_and_deterministic() -> None:
    records = [
        {
            "id": "2",
            "kind": "claim",
            "title": "A claim about agents",
            "text": "agents agents",
            "tags": [],
            "channel": "Channel",
        },
        {
            "id": "1",
            "kind": "video",
            "title": "Agents",
            "text": "A short record",
            "tags": [],
            "channel": "Channel",
        },
        {
            "id": "3",
            "kind": "video",
            "title": "Something else",
            "text": "agents appear here",
            "tags": [],
            "channel": "Channel",
        },
    ]

    ranked = rank_search_records("agents", records)

    assert [record["id"] for record in ranked] == ["1", "2", "3"]
    assert rank_search_records("agents missing", records) == []
