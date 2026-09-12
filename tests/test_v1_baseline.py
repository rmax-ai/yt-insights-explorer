from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from yt_insights_web.build import build_site
from yt_insights_web.load import load_corpus
from yt_insights_web.models import INSIGHT_SECTIONS
from yt_insights_web.normalize import normalize_corpus
from yt_insights_web.serialize import json_text

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "v1-edge"
GOLDEN_DIR = ROOT / "tests" / "fixtures" / "golden"
DATA_GOLDEN = GOLDEN_DIR / "v1-edge-data.json"
SITE_MANIFEST_GOLDEN = GOLDEN_DIR / "v1-edge-site-manifest.json"
VIDEO_ID = "V1Edge9xYzA"
REPEATED_EVIDENCE = "重复证据 e\u0301 🧭"
UNICODE_SAMPLES = ("多言語", "e\u0301", "🧭")


def _manifest(path: Path) -> dict[str, str]:
    files = sorted(
        (file for file in path.rglob("*") if file.is_file()),
        key=lambda file: file.relative_to(path).as_posix(),
    )
    return {
        file.relative_to(path).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in files
    }


def _normalized_envelope() -> dict[str, object]:
    normalized = normalize_corpus(load_corpus(FIXTURE))
    return {
        "videos": normalized.videos,
        "concepts": normalized.concepts,
        "index_items": normalized.index_items,
        "warnings": normalized.warnings,
    }


def _maybe_regenerate(path: Path, content: str) -> None:
    """Refresh committed goldens only after an intentional V1 baseline review."""

    if os.environ.get("REGEN_V1_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")


def test_v1_edge_fixture_covers_all_sections_and_edge_values(tmp_path: Path) -> None:
    index = json.loads((FIXTURE / "index.json").read_text(encoding="utf-8"))
    summary = (FIXTURE / "artifacts" / "v1-edge-video" / "summary.md").read_text(
        encoding="utf-8"
    )
    insights = json.loads(
        (FIXTURE / "artifacts" / "v1-edge-video" / "insights.json").read_text(encoding="utf-8")
    )

    item = index["items"][0]
    assert item["video_id"] == VIDEO_ID
    assert item["status"] == "analyzed"
    assert item["artifacts"]["transcript"] is None
    assert item["artifacts"]["summary"] == "artifacts/v1-edge-video/summary.md"
    assert item["artifacts"]["insights"] == "artifacts/v1-edge-video/insights.json"
    assert item["cost_usd_total"] is None
    assert f"https://www.youtube.com/watch?v={VIDEO_ID}" in summary

    timestamped_quotes = re.findall(r'^> "[^"]+" \(at \d+:\d{2}\)$', summary, re.MULTILINE)
    assert timestamped_quotes == [
        '> "The whole system reveals behavior that its parts cannot show." (at 1:23)'
    ]
    assert set(insights) == set(INSIGHT_SECTIONS) | {"tags"}
    assert insights["article_ideas"] == []
    assert insights["deep_dives"][0]["evidence_quotes"] == []
    assert insights["key_claims"][0]["verification_question"] is None

    assert insights["core_insights"][0]["evidence_quotes"] == [
        REPEATED_EVIDENCE,
        REPEATED_EVIDENCE,
    ]

    envelope = _normalized_envelope()
    assert set(envelope) == {"videos", "concepts", "index_items", "warnings"}
    assert envelope["index_items"][0]["cost_usd_total"] is None
    video = envelope["videos"][0]
    normalized_core_quotes = video["core_insights"][0]["evidence_quotes"]
    # The repeated string is intentionally ambiguous; E1-T2 must not recover its timestamp.
    repeated_quotes = [
        quote for quote in normalized_core_quotes if quote["text"] == REPEATED_EVIDENCE
    ]
    assert len(repeated_quotes) == 2
    assert repeated_quotes[0] == repeated_quotes[1]
    assert all(quote["timestamp_seconds"] is None for quote in repeated_quotes)
    assert all(quote["source_url"] is None for quote in repeated_quotes)
    all_quotes = [
        quote
        for section in ("core_insights", "deep_dives")
        for item in video[section]
        for quote in item["evidence_quotes"]
    ]
    all_quotes.append(video["tradeoffs_and_failure_modes"][0]["evidence_quote"])
    assert all(
        quote["timestamp_seconds"] is None and quote["source_url"] is None
        for quote in all_quotes
    )
    # An empty top-level section and an empty nested evidence list cover distinct meanings.
    assert video["article_ideas"] == []
    assert video["deep_dives"][0]["evidence_quotes"] == []
    assert video["key_claims"][0]["verification_question"] is None

    normalized_text = json_text(envelope)
    assert all(sample in normalized_text for sample in UNICODE_SAMPLES)

    output = tmp_path / "site"
    build_site(FIXTURE, output, generated_at=None)
    video_page = output / video["url"]
    rendered = video_page.read_text(encoding="utf-8")
    assert all(sample in rendered for sample in UNICODE_SAMPLES)
    assert rendered.count(REPEATED_EVIDENCE) == 2


def test_v1_normalized_data_matches_golden() -> None:
    actual = json_text(_normalized_envelope())
    _maybe_regenerate(DATA_GOLDEN, actual)

    assert actual == DATA_GOLDEN.read_text(encoding="utf-8")


def test_v1_site_manifest_matches_golden(tmp_path: Path) -> None:
    output = tmp_path / "site"
    build_site(FIXTURE, output, generated_at=None)
    actual = json_text(_manifest(output))
    _maybe_regenerate(SITE_MANIFEST_GOLDEN, actual)

    assert actual == SITE_MANIFEST_GOLDEN.read_text(encoding="utf-8")


def test_repeated_v1_build_is_byte_identical(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    build_site(FIXTURE, first, generated_at=None)
    build_site(FIXTURE, second, generated_at=None)

    first_paths = set(_manifest(first))
    second_paths = set(_manifest(second))
    assert first_paths == second_paths
    for relative_path in sorted(first_paths):
        assert (first / relative_path).read_bytes() == (second / relative_path).read_bytes()


def test_v1_build_does_not_mutate_fixture(tmp_path: Path) -> None:
    before = _manifest(FIXTURE)
    build_site(FIXTURE, tmp_path / "site", generated_at=None)

    assert _manifest(FIXTURE) == before
