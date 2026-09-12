from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from yt_insights_web.corpus.compiler import compile_corpus
from yt_insights_web.derive import (
    derive_claims,
    derive_ideas,
    derive_trends,
    month_axis,
)
from yt_insights_web.load import load_corpus
from yt_insights_web.normalize import normalize_corpus
from yt_insights_web.serialize import json_text

ROOT = Path(__file__).resolve().parents[1]
CONSUMER_FIXTURE = ROOT / "tests" / "fixtures" / "consumer-cutover"


def video(
    video_id: str,
    month: str,
    tags: list[str],
    *,
    insight_type: str = "architecture",
) -> dict:
    return {
        "video_id": video_id,
        "title": f"Video {video_id}",
        "channel": "Channel",
        "url": f"videos/{video_id}/index.html",
        "source": {
            "published_at": f"{month}-15T00:00:00Z",
            "published_date": f"{month}-15",
            "published_month": month,
        },
        "tags": [
            {"name": tag, "concept_id": f"c-{tag}", "url": f"concepts/{tag}/index.html"}
            for tag in tags
        ],
        "core_insights": [
            {
                "id": f"{video_id}:core_insights:0",
                "source_index": 0,
                "insight": f"Insight for {video_id}",
                "type": insight_type,
                "why_it_matters": "why",
                "generalization": "general",
                "evidence_quotes": [],
                "evidence_strength": "strong",
                "novelty": "high",
            }
        ],
        "article_ideas": [],
        "project_ideas": [],
        "deep_dives": [],
        "open_questions": [],
        "key_claims": [],
        "connections": [],
    }


def test_month_axis_is_continuous() -> None:
    videos = [video("a", "2021-01", []), video("b", "2021-03", [])]

    assert month_axis(videos) == ["2021-01", "2021-02", "2021-03"]


def test_trends_use_distinct_video_tags_and_no_fake_rising_without_baseline() -> None:
    videos = [
        video("a", "2026-01", ["alpha", "alpha"]),
        video("b", "2026-02", ["alpha"]),
        video("c", "2026-03", ["alpha"]),
    ]

    trends = derive_trends(videos)
    alpha = next(item for item in trends["tag_rankings"] if item["name"] == "alpha")

    assert trends["tag_monthly"][0]["counts"] == [1, 1, 1]
    assert alpha["video_count"] == 3
    assert alpha["recent_count"] == 3
    assert alpha["prior_count"] == 0
    assert alpha["rising"] is False


def test_trend_window_counts_calendar_months_and_ranks_rising() -> None:
    videos = [
        video("old", "2025-10", ["steady"]),
        video("one", "2025-11", ["rising"]),
        video("two", "2025-12", ["rising"]),
        video("three", "2026-01", ["rising"]),
        video("four", "2026-01", ["rising"]),
    ]

    alpha = next(item for item in derive_trends(videos)["tag_rankings"] if item["name"] == "rising")

    assert alpha["recent_count"] == 4
    assert alpha["prior_count"] == 0
    assert alpha["recent_rate"] == 1.0
    assert alpha["rising"] is True


def test_type_and_idea_monthly_counts() -> None:
    first = video("a", "2026-01", [], insight_type="mechanism")
    first["article_ideas"] = [{"title": "article"}]
    first["project_ideas"] = [{"name": "project"}]
    second = video("b", "2026-02", [], insight_type="architecture")
    second["deep_dives"] = [{"topic": "deep"}]
    second["open_questions"] = [{"question": "question"}]

    trends = derive_trends([first, second])

    assert trends["insight_type_monthly"][0]["counts"]["mechanism"] == 1
    assert trends["idea_flow_monthly"][0]["article_ideas"] == 1
    assert trends["idea_flow_monthly"][1]["deep_dives"] == 1


def test_aggregate_ideas_and_claims_keep_video_provenance() -> None:
    source = video("a", "2026-01", [])
    source["article_ideas"] = [
        {"id": "a:article_ideas:0", "source_index": 0, "title": "Write this"}
    ]
    source["key_claims"] = [
        {
            "id": "a:key_claims:0",
            "source_index": 0,
            "claim": "A claim",
            "claim_type": "factual",
            "evidence": "Evidence",
            "verification_needed": True,
            "verification_question": "Verify?",
            "verification_status": "needed",
        }
    ]

    ideas = derive_ideas([source])
    claims = derive_claims([source])

    assert ideas["article_ideas"][0]["video_id"] == "a"
    assert ideas["article_ideas"][0]["video_url"] == "videos/a/index.html"
    assert claims["claims"][0]["video_title"] == "Video a"
    assert claims["claims"][0]["verification_status"] == "needed"


def _consumer_corpus():
    loaded = load_corpus(CONSUMER_FIXTURE)
    return compile_corpus(loaded.videos, overlay_root=CONSUMER_FIXTURE)


def test_canonical_topics_preserve_namespace_resolution_and_unknown_nodes() -> None:
    corpus = _consumer_corpus()

    trends = derive_trends(corpus, compatibility=False)
    topic_rows = {row["topic_id"]: row for row in trends["topic_rankings"]}

    assert topic_rows["topic_canonical"]["name"] == "Canonical Topic"
    assert topic_rows["topic_canonical"]["resolutions"][0]["canonical_id"] == "topic_canonical"
    unresolved = next(row for row in topic_rows.values() if row["name"] == "Unknown Legacy")
    assert unresolved["topic_id"].startswith("topic-")
    assert unresolved["resolutions"][0]["unresolved"] is True
    assert unresolved["topic_id"] != "concept_canonical"


def test_canonical_claims_keep_request_and_ledger_state_separate() -> None:
    claims = derive_claims(_consumer_corpus(), compatibility=False)["claims"]
    by_claim = {claim["claim"]: claim for claim in claims}

    assert by_claim["A resolved label still needs source provenance."][
        "verification_requested"
    ] is True
    assert (
        by_claim["A resolved label still needs source provenance."]["verification_needed"] is True
    )
    assert (
        by_claim["A resolved label still needs source provenance."]["review_status"] == "verified"
    )
    assert (
        by_claim["Unresolved labels remain deterministic nodes."]["review_status"]
        == "unreviewed"
    )
    assert (
        by_claim["Topic IDs and concept IDs are separate namespaces."]["review_status"]
        == "refuted"
    )


def test_canonical_v1_compatibility_projection_matches_legacy_derivation() -> None:
    loaded = load_corpus(CONSUMER_FIXTURE)
    legacy = normalize_corpus(loaded)
    canonical = compile_corpus(loaded.videos, overlay_root=CONSUMER_FIXTURE)

    assert json_text(derive_trends(legacy.videos)) == json_text(
        derive_trends(canonical, compatibility=True)
    )
    assert json_text(derive_ideas(legacy.videos)) == json_text(
        derive_ideas(canonical, compatibility=True)
    )
    assert json_text(derive_claims(legacy.videos)) == json_text(
        derive_claims(canonical, compatibility=True)
    )


def test_reversing_canonical_video_and_section_inputs_is_byte_identical() -> None:
    loaded = load_corpus(CONSUMER_FIXTURE)
    corpus = compile_corpus(loaded.videos, overlay_root=CONSUMER_FIXTURE)
    reversed_videos = tuple(
        replace(
            video,
            core_insights=tuple(reversed(video.core_insights)),
            article_ideas=tuple(reversed(video.article_ideas)),
            deep_dives=tuple(reversed(video.deep_dives)),
            key_claims=tuple(reversed(video.key_claims)),
        )
        for video in reversed(corpus.videos)
    )
    reversed_corpus = replace(corpus, videos=reversed_videos)

    for derive in (derive_trends, derive_ideas, derive_claims):
        assert json_text(derive(corpus, compatibility=False)) == json_text(
            derive(reversed_corpus, compatibility=False)
        )
