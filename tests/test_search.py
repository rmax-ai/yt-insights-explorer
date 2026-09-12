from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from yt_insights_web.corpus.compiler import compile_corpus
from yt_insights_web.load import load_corpus
from yt_insights_web.search import build_search_records, rank_search_records
from yt_insights_web.serialize import json_text

ROOT = Path(__file__).resolve().parents[1]
V1_FIXTURE = ROOT / "tests" / "fixtures" / "v1-edge"
V2_FIXTURE = ROOT / "tests" / "fixtures" / "v2-contract"
CONSUMER_FIXTURE = ROOT / "tests" / "fixtures" / "consumer-cutover"


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


def _write_v2_source(tmp_path: Path, video_id: str = "v2-search") -> Path:
    source = tmp_path / "v2-source"
    shutil.copytree(V1_FIXTURE, source)
    index_path = source / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["items"][0]["video_id"] = video_id
    index_path.write_text(json.dumps(index) + "\n", encoding="utf-8")
    summary_md = source / "artifacts" / "v1-edge-video" / "summary.md"
    summary_md.write_text(
        summary_md.read_text(encoding="utf-8").replace("V1Edge9xYzA", video_id),
        encoding="utf-8",
    )
    for filename in ("summary.json", "insights.json"):
        payload = json.loads(
            (V2_FIXTURE / filename.replace(".json", "-golden.json")).read_text(
                encoding="utf-8"
            )
        )
        text = json.dumps(payload).replace("v2-contract-001", video_id)
        (source / "artifacts" / "v1-edge-video" / filename).write_text(
            text,
            encoding="utf-8",
        )
    return source


def test_canonical_search_uses_explicit_occurrence_and_concept_ids() -> None:
    loaded = load_corpus(CONSUMER_FIXTURE)
    corpus = compile_corpus(loaded.videos, overlay_root=CONSUMER_FIXTURE)

    records = build_search_records(corpus)
    claim = next(record for record in records if record["kind"] == "claim")
    video = next(record for record in records if record["kind"] == "video")
    concept = next(
        record
        for record in records
        if record["kind"] == "concept" and record["concept_id"] == "concept_canonical"
    )

    assert claim["occurrence_id"] == "ConsumerV1abc:key_claims:0"
    assert claim["occurrence_id_kind"] == "legacy-position"
    assert "id" not in claim
    assert "concept_canonical" in claim["concept_ids"]
    assert "c-connection-only-1ea0cc57" in video["concept_ids"]
    assert concept["concept_id"] == "concept_canonical"
    assert concept["canonical_id"] == "concept_canonical"


def test_mixed_v1_v2_search_keeps_legacy_and_persisted_occurrence_ids(
    tmp_path: Path,
) -> None:
    v1 = load_corpus(V1_FIXTURE).videos[0]
    v2 = load_corpus(_write_v2_source(tmp_path)).videos[0]
    corpus = compile_corpus((v1, v2))

    records = build_search_records(corpus)
    claims = [record for record in records if record["kind"] == "claim"]
    occurrence_kinds = {
        record["occurrence_id"]: record["occurrence_id_kind"] for record in claims
    }

    assert occurrence_kinds["V1Edge9xYzA:key_claims:0"] == "legacy-position"
    assert occurrence_kinds["claim-v1:v2-search:key-claim-1"] == "persisted"
    assert all("concept_ids" in record for record in records)
    assert all("tags" not in record for record in records)


def test_search_rejects_global_occurrence_and_concept_id_collision() -> None:
    source = load_corpus(CONSUMER_FIXTURE).videos[0]
    collision = replace(source, index=replace(source.index, video_id="collision"))
    documents = {
        "concepts": {
            "schema_version": 1,
            "concepts": [
                {
                    "id": "collision",
                    "canonical_name": "Raw Alias",
                    "aliases": [],
                    "status": "canonical",
                }
            ],
        },
        "topics": {"schema_version": 1, "topics": []},
        "projects": {"schema_version": 1, "projects": []},
        "claims": {"schema_version": 1, "reviews": []},
    }
    corpus = compile_corpus((collision,), overlays=documents)

    with pytest.raises(ValueError, match="global ID collision"):
        build_search_records(corpus)


def test_reversing_canonical_search_inputs_is_byte_identical(tmp_path: Path) -> None:
    v1 = load_corpus(V1_FIXTURE).videos[0]
    v2 = load_corpus(_write_v2_source(tmp_path)).videos[0]
    corpus = compile_corpus((v1, v2))
    reversed_videos = tuple(
        replace(video, key_claims=tuple(reversed(video.key_claims)))
        for video in reversed(corpus.videos)
    )
    reversed_corpus = replace(corpus, videos=reversed_videos)

    assert json_text(build_search_records(corpus)) == json_text(
        build_search_records(reversed_corpus)
    )
