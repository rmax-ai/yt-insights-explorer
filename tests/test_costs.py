from __future__ import annotations

import io
import json
from pathlib import Path

from yt_insights_web.costs import build_cost_report, render_costs_markdown


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _processed(
    video_id: str,
    *,
    duration: str | None,
    ingested_at: str,
    summarize: dict | None = None,
    analyze: dict | None = None,
) -> dict:
    costs = {}
    if summarize is not None:
        costs["summarize"] = summarize
    if analyze is not None:
        costs["analyze"] = analyze
    return {
        "video_id": video_id,
        "title": f"Video {video_id}",
        "channel": "Fixture channel",
        "published_at": "2026-01-01T00:00:00Z",
        "ingested_at": ingested_at,
        "status": "analyzed",
        "metadata": {} if duration is None else {"duration": duration},
        "costs": costs,
    }


def _cost(model: str, amount: float) -> dict:
    return {
        "model": model,
        "input_tokens": 10,
        "output_tokens": 20,
        "cached_tokens": 3,
        "calls": 1,
        "cost_usd": amount,
    }


def make_fixture(root: Path) -> tuple[Path, Path, dict]:
    source = root / "source"
    index_items = [
        {
            "video_id": "a",
            "title": "Video a",
            "channel": "Fixture channel",
            "status": "analyzed",
            "ingested_at": "2026-01-02T00:00:00Z",
        },
        {
            "video_id": "b",
            "title": "Video b",
            "channel": "Fixture channel",
            "status": "analyzed",
            "ingested_at": "2026-02-02T00:00:00Z",
        },
        {
            "video_id": "c",
            "title": "Video c",
            "channel": "Fixture channel",
            "status": "analyzed",
            "ingested_at": "2026-02-03T00:00:00Z",
        },
        {
            "video_id": "d",
            "title": "No costs",
            "channel": "Fixture channel",
            "status": "analyzed",
        },
        {"video_id": "skipped", "status": "skipped"},
        {"video_id": "failed", "status": "failed"},
    ]
    _write_json(source / "index.json", {"items": index_items})
    _write_json(
        source / "processed" / "a.json",
        _processed(
            "a",
            duration="PT45M",
            ingested_at="2026-01-02T00:00:00Z",
            summarize=_cost("model-a", 1.23456789),
            analyze=_cost("model-b", 0.2),
        ),
    )
    _write_json(
        source / "processed" / "b.json",
        _processed(
            "b",
            duration="PT2H30M",
            ingested_at="2026-02-02T00:00:00Z",
            summarize=_cost("model-a", 0.3333333),
        ),
    )
    _write_json(
        source / "processed" / "c.json",
        _processed(
            "c",
            duration=None,
            ingested_at="2026-02-03T00:00:00Z",
            summarize=_cost("model-a", 0.1),
        ),
    )
    _write_json(source / "processed" / "d.json", {"video_id": "d", "costs": {}})
    _write_json(source / "processed" / "broken.json", {"not": "used"})

    gates = root / "gates" / "recommendations"
    _write_json(
        gates / "one.json",
        {"meta": {"cost_usd": 0.12345678, "scored_at": "2026-01-20T00:00:00Z"}},
    )
    _write_json(
        gates / "two.json",
        {"meta": {"cost_usd": 0.2, "scored_at": "2026-02-20T00:00:00Z"}},
    )
    ops = {
        "agent_ops_usd": 0.4567891,
        "operations": [
            {"component": "fixture job", "runs_as": "cron", "cost": "$0.01"}
        ],
    }
    return source, root / "gates", ops


def test_cost_report_aggregates_fixture_data(tmp_path: Path) -> None:
    source, gates, ops = make_fixture(tmp_path)
    warning = io.StringIO()

    report = build_cost_report(
        source,
        gates=gates,
        operations=ops,
        generated_at="2026-10-06T12:00:00Z",
        warning=warning,
    )

    assert report["source"] == {
        "index_items": 6,
        "analyzed": 4,
        "skipped": 1,
        "failed": 1,
        "with_cost_records": 3,
    }
    assert report["totals"] == {
        "processing_usd": 1.867901,
        "scoring_usd": 0.323457,
        "agent_ops_usd": 0.456789,
    }
    assert report["stages"] == [
        {
            "stage": "summarize",
            "videos": 3,
            "total_usd": 1.667901,
            "avg_usd": 0.555967,
            "models": {"model-a": 3},
        },
        {
            "stage": "analyze",
            "videos": 1,
            "total_usd": 0.2,
            "avg_usd": 0.2,
            "models": {"model-b": 1},
        },
    ]
    assert report["gates"] == {
        "scored": 2,
        "total_usd": 0.323457,
        "avg_usd": 0.161728,
        "by_month": {
            "2026-01": {"scored": 1, "total_usd": 0.123457},
            "2026-02": {"scored": 1, "total_usd": 0.2},
        },
    }
    assert report["duration_buckets"] == [
        {"label": "up to 45 min", "videos": 1, "total_usd": 1.434568, "avg_usd": 1.434568},
        {"label": "45 min - 2.5 h", "videos": 1, "total_usd": 0.333333, "avg_usd": 0.333333},
        {"label": "2.5 h +", "videos": 0, "total_usd": 0, "avg_usd": 0.0},
        {"label": "unknown", "videos": 1, "total_usd": 0.1, "avg_usd": 0.1},
    ]
    assert report["monthly"] == [
        {"month": "2026-01", "videos": 1, "total_usd": 1.434568, "avg_usd": 1.434568},
        {"month": "2026-02", "videos": 2, "total_usd": 0.433333, "avg_usd": 0.216667},
    ]
    assert report["distribution"] == {
        "median_usd": 0.333333,
        "p90_usd": 1.214321,
        "p95_usd": 1.324444,
        "p99_usd": 1.412543,
        "max_usd": 1.434568,
    }
    assert report["operations"] == ops["operations"]
    assert report["per_video"][0]["video_id"] == "a"
    assert report["per_video"][0]["summarize"]["input_tokens"] == 10
    assert report["per_video"][1]["analyze"] is None
    assert "no usable cost stages" in warning.getvalue()


def test_cost_report_is_deterministic_and_markdown_is_conditional(tmp_path: Path) -> None:
    source, gates, ops = make_fixture(tmp_path)
    first = build_cost_report(source, gates=gates, operations=ops, generated_at="same")
    second = build_cost_report(source, gates=gates, operations=ops, generated_at="same")
    assert first == second

    markdown = render_costs_markdown(first)
    assert markdown.startswith("<!-- generated by costs-report; do not edit by hand -->")
    assert "Data as of **same**." in markdown
    assert "## Relevance scoring" in markdown
    assert "## Operations layer" in markdown
    assert "$1.435" in markdown
    assert "77%" in markdown

    without_optional = build_cost_report(source, generated_at="2026-10-06T00:00:00Z")
    markdown_without_optional = render_costs_markdown(without_optional)
    assert "## Relevance scoring" not in markdown_without_optional
    assert "## Operations layer" not in markdown_without_optional
