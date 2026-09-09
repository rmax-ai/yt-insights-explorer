from __future__ import annotations

import json
from pathlib import Path

from yt_insights_web.load import load_corpus
from yt_insights_web.normalize import normalize_corpus

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "corpus"


def test_normalization_emits_video_contract() -> None:
    normalized = normalize_corpus(load_corpus(FIXTURE))
    video = next(video for video in normalized.videos if video["video_id"] == "r8RGYw1n-5k")

    assert video["schema_version"] == 1
    assert video["title"].startswith("What Everyone Missed")
    assert video["source"]["published_month"] == "2025-11"
    assert video["source"]["published_date"] == "2025-11-20"
    assert video["document"]["visibility"] == "private"
    assert video["document"]["review_status"] == "unreviewed"
    assert video["core_insights"][0]["id"] == "r8RGYw1n-5k:core_insights:0"
    assert video["core_insights"][0]["source_index"] == 0
    assert video["core_insights"][0]["evidence_quotes"][0]["timestamp_seconds"] is None
    assert video["core_insights"][0]["evidence_quotes"][0]["source_url"] is None


def test_tags_union_case_and_whitespace_variants() -> None:
    normalized = normalize_corpus(load_corpus(FIXTURE))
    r8 = next(item for item in normalized.videos if item["video_id"] == "r8RGYw1n-5k")
    names = {tag["name"].casefold() for tag in r8["tags"]}

    assert {"gemini 3", "ai agents", "infinite-context", "cost-deflation"} <= names
    assert len(names) == len(r8["tags"])


def test_connection_only_concepts_are_retained() -> None:
    normalized = normalize_corpus(load_corpus(FIXTURE))
    names = {concept["name"].casefold() for concept in normalized.concepts}

    assert "application runtime" in names
    runtime = next(
        concept
        for concept in normalized.concepts
        if concept["name"].casefold() == "application runtime"
    )
    assert runtime["tag_video_count"] == 0


def test_serialized_normalized_video_has_no_source_paths() -> None:
    normalized = normalize_corpus(load_corpus(FIXTURE))
    encoded = json.dumps(normalized.videos, ensure_ascii=False)

    assert "summary_path" not in encoded
    assert "insights_path" not in encoded
    assert "/home/" not in encoded
