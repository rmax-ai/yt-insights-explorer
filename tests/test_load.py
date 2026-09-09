from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from yt_insights_web.load import CorpusValidationError, load_corpus

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "corpus"


def copy_fixture(tmp_path: Path) -> Path:
    destination = tmp_path / "corpus"
    shutil.copytree(FIXTURE, destination)
    return destination


def test_loads_analyzed_fixture_years() -> None:
    corpus = load_corpus(FIXTURE)

    assert len(corpus.index_items) == 3
    assert len(corpus.videos) == 3
    assert {video.video_id for video in corpus.videos} == {
        "r8RGYw1n-5k",
        "sL3QPAYoB6s",
        "N5RPTlRR9eY",
    }
    assert all(video.summary_path.startswith("artifacts/") for video in corpus.videos)


def test_malformed_root_is_actionable(tmp_path: Path) -> None:
    source = copy_fixture(tmp_path)
    (source / "index.json").write_text("[]\n", encoding="utf-8")

    with pytest.raises(CorpusValidationError, match="index.json.*object"):
        load_corpus(source)


def test_missing_artifact_and_path_escape_are_rejected(tmp_path: Path) -> None:
    source = copy_fixture(tmp_path)
    index = json.loads((source / "index.json").read_text(encoding="utf-8"))
    index["items"][0]["artifacts"]["summary"] = "../summary.md"
    (source / "index.json").write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises(CorpusValidationError, match="beneath source artifacts"):
        load_corpus(source)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda data: data.pop("deep_dives"),
            "missing required top-level section",
        ),
        (
            lambda data: data.__setitem__("core_insights", {}),
            "must be a list",
        ),
        (
            lambda data: data["key_claims"][0].__setitem__("claim_type", "rumor"),
            "unknown enum",
        ),
    ],
)
def test_nested_schema_errors_are_reported(tmp_path: Path, mutation, message: str) -> None:
    source = copy_fixture(tmp_path)
    index = json.loads((source / "index.json").read_text(encoding="utf-8"))
    insights_path = source / index["items"][0]["artifacts"]["insights"]
    insights = json.loads(insights_path.read_text(encoding="utf-8"))
    mutation(insights)
    insights_path.write_text(json.dumps(insights), encoding="utf-8")

    with pytest.raises(CorpusValidationError, match=message):
        load_corpus(source)


def test_invalid_date_is_rejected(tmp_path: Path) -> None:
    source = copy_fixture(tmp_path)
    index = json.loads((source / "index.json").read_text(encoding="utf-8"))
    summary_path = source / index["items"][0]["artifacts"]["summary"]
    text = summary_path.read_text(encoding="utf-8").replace(
        "source_published: '2025-11-20T21:01:05Z'",
        "source_published: 'not-a-date'",
    )
    summary_path.write_text(text, encoding="utf-8")

    with pytest.raises(CorpusValidationError, match="source_published.*ISO"):
        load_corpus(source)


def test_frontmatter_disagreement_is_a_warning(tmp_path: Path) -> None:
    source = copy_fixture(tmp_path)
    index = json.loads((source / "index.json").read_text(encoding="utf-8"))
    summary_path = source / index["items"][0]["artifacts"]["summary"]
    text = summary_path.read_text(encoding="utf-8").replace(
        "source_author: Maxi",
        "source_author: Other channel",
    )
    summary_path.write_text(text, encoding="utf-8")

    corpus = load_corpus(source)

    assert any("author" in warning.lower() for warning in corpus.warnings)


def test_skipped_and_failed_records_are_not_loaded(tmp_path: Path) -> None:
    source = copy_fixture(tmp_path)
    index = json.loads((source / "index.json").read_text(encoding="utf-8"))
    index["items"].extend(
        [
            {
                "video_id": "skipped",
                "title": "Skipped",
                "channel": "Maxi",
                "status": "skipped",
                "ingested_at": "2026-01-01T00:00:00Z",
                "artifacts": {"summary": None, "insights": None},
                "cost_usd_total": None,
            },
            {
                "video_id": "failed",
                "title": "Failed",
                "channel": "Maxi",
                "status": "failed",
                "ingested_at": "2026-01-01T00:00:00Z",
                "artifacts": {"summary": None, "insights": None},
                "cost_usd_total": None,
            },
        ]
    )
    (source / "index.json").write_text(json.dumps(index), encoding="utf-8")

    corpus = load_corpus(source)

    assert len(corpus.index_items) == 5
    assert {video.video_id for video in corpus.videos} == {
        "r8RGYw1n-5k",
        "sL3QPAYoB6s",
        "N5RPTlRR9eY",
    }
