from __future__ import annotations

from yt_insights_web.slug import (
    concept_id,
    concept_slug,
    item_id,
    slugify,
    video_slug,
)


def test_slugify_is_url_safe_and_deterministic() -> None:
    assert slugify("  What Everyone Missed — Gemini 3!  ") == "what-everyone-missed-gemini-3"
    assert slugify("Café / naïve") == "cafe-naive"
    assert slugify("東京") == "item"


def test_video_and_concept_slugs_include_collision_resistant_identity() -> None:
    assert video_slug("Example Video", "AbC_123") == "example-video-abc-123"
    assert concept_slug("AI Agents") == "ai-agents-e748f875"
    assert concept_slug("ai  agents") == concept_slug("AI Agents")
    assert concept_id("AI Agents").startswith("c-ai-agents-")
    assert concept_id("AI Agents") != concept_id("Different")


def test_item_ids_use_source_order() -> None:
    assert item_id("video", "core_insights", 0) == "video:core_insights:0"
    assert item_id("video", "key_claims", 4) == "video:key_claims:4"
