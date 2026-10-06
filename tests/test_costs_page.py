from __future__ import annotations

import json
from pathlib import Path

from yt_insights_web.build import build_site
from yt_insights_web.verify import verify_site

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "corpus"


def costs_fixture() -> dict:
    return {
        "schema_version": 1,
        "generated_at": "2026-10-06T12:00:00Z",
        "currency": "USD",
        "source": {
            "index_items": 3,
            "analyzed": 3,
            "skipped": 0,
            "failed": 0,
            "with_cost_records": 2,
        },
        "totals": {
            "processing_usd": 1.234567,
            "scoring_usd": 0.123456,
            "agent_ops_usd": None,
        },
        "stages": [
            {
                "stage": "summarize",
                "videos": 2,
                "total_usd": 1.0,
                "avg_usd": 0.5,
                "models": {"fixture-model": 2},
            },
            {
                "stage": "analyze",
                "videos": 2,
                "total_usd": 0.234567,
                "avg_usd": 0.117284,
                "models": {"fixture-model": 2},
            },
        ],
        "gates": {
            "scored": 4,
            "total_usd": 0.123456,
            "avg_usd": 0.030864,
            "by_month": {"2026-10": {"scored": 4, "total_usd": 0.123456}},
        },
        "distribution": {
            "median_usd": 0.617284,
            "p90_usd": 1.0,
            "p95_usd": 1.0,
            "p99_usd": 1.0,
            "max_usd": 1.0,
        },
        "duration_buckets": [
            {"label": "up to 45 min", "videos": 1, "total_usd": 0.2, "avg_usd": 0.2},
            {"label": "45 min - 2.5 h", "videos": 1, "total_usd": 1.034567, "avg_usd": 1.034567},
            {"label": "2.5 h +", "videos": 0, "total_usd": 0.0, "avg_usd": 0.0},
            {"label": "unknown", "videos": 0, "total_usd": 0.0, "avg_usd": 0.0},
        ],
        "monthly": [
            {"month": "2026-10", "videos": 2, "total_usd": 1.234567, "avg_usd": 0.617284}
        ],
        "rates": [
            {
                "model": "fixture-model",
                "input_per_1m": 0.3,
                "output_per_1m": 2.5,
                "cached_input_per_1m": 0.03,
            }
        ],
        "per_video": [
            {
                "video_id": "fixture-a",
                "title": "Most expensive fixture video",
                "channel": "Fixture channel",
                "published_at": "2026-10-01T00:00:00Z",
                "duration_s": 3600,
                "bucket": "45 min - 2.5 h",
                "total_usd": 1.0,
                "summarize": None,
                "analyze": None,
            }
        ],
    }


def test_costs_page_and_raw_json_are_emitted(tmp_path: Path) -> None:
    costs_path = tmp_path / "costs.json"
    raw = json.dumps(costs_fixture(), ensure_ascii=False, indent=2) + "\n"
    costs_path.write_text(raw, encoding="utf-8")
    output = tmp_path / "with-costs"

    build_site(FIXTURE, output, costs_json=costs_path)

    page = (output / "costs" / "index.html").read_text(encoding="utf-8")
    assert (output / "costs" / "index.html").is_file()
    assert (output / "costs" / "costs.json").read_text(encoding="utf-8") == raw
    assert "Most expensive fixture video" in page
    assert "$1.234567" in page
    assert "Download costs.json" in page
    assert 'href="costs/index.html"' in (output / "index.html").read_text(encoding="utf-8")
    verify_site(output)


def test_build_without_costs_has_no_costs_output(tmp_path: Path) -> None:
    output = tmp_path / "without-costs"

    build_site(FIXTURE, output)

    assert not (output / "costs").exists()
    assert "Costs" not in (output / "index.html").read_text(encoding="utf-8")
