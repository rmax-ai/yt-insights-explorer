"""Collect and render the pipeline's captured cost evidence.

The report intentionally reads only ``index.json``, ``processed/*.json`` and
optional relevance-gate JSON files.  It never opens transcript or artifact
files.
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

# Source of truth: /home/rmax-10/src/rmax-ai/yt-insights/src/yt_insights/costs.py.
# Refresh these values from that file (and its provider-pricing references)
# whenever the pipeline price table changes; do not import from the sibling
# checkout.
PRICE_USD_PER_1M: dict[str, dict[str, float]] = {
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50, "cached_input": 0.03},
    "gemini-3.5-flash-lite": {"input": 0.30, "output": 2.50, "cached_input": 0.03},
    "deepseek-flash": {"input": 0.15, "output": 0.60, "cached_input": 0.003},
    "deepseek-v4-flash": {"input": 0.15, "output": 0.60, "cached_input": 0.003},
    "deepseek-v4-pro": {"input": 0.66, "output": 1.98, "cached_input": 0.022},
}

DURATION_BUCKETS = (
    "up to 45 min",
    "45 min - 2.5 h",
    "2.5 h +",
    "unknown",
)
STAGES = ("summarize", "analyze")
_DURATION_RE = re.compile(
    r"^PT(?:(?P<hours>\d+(?:\.\d+)?)H)?"
    r"(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?$"
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _warning(warning: TextIO, message: str) -> None:
    print(f"warning: {message}", file=warning)


def _read_json(path: Path, warning: TextIO) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _warning(warning, f"skipping malformed JSON {path}: {exc}")
        return None


def _parse_duration(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    match = _DURATION_RE.fullmatch(value)
    if match is None or not any(match.groupdict().values()):
        return None
    hours = float(match.group("hours") or 0)
    minutes = float(match.group("minutes") or 0)
    seconds = float(match.group("seconds") or 0)
    result = hours * 3600 + minutes * 60 + seconds
    if not math.isfinite(result) or result < 0:
        return None
    return result


def _duration_bucket(duration_s: float | None) -> str:
    if duration_s is None:
        return "unknown"
    if duration_s <= 45 * 60:
        return "up to 45 min"
    if duration_s <= 2.5 * 60 * 60:
        return "45 min - 2.5 h"
    return "2.5 h +"


def _parse_date(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)


def _month(value: Any) -> str | None:
    parsed = _parse_date(value)
    return parsed.strftime("%Y-%m") if parsed else None


def _round(value: float) -> float:
    return round(value, 6)


def _percentile(values: list[float], percentile: float) -> float:
    """Return a linearly interpolated percentile, including odd counts."""

    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _model(value: Any) -> str:
    return value if isinstance(value, str) and value else "unknown"


def _cost_from_usage(model: str, record: dict[str, Any]) -> float | None:
    prices = PRICE_USD_PER_1M.get(model)
    if prices is None:
        return None
    input_tokens = _number(record.get("input_tokens"))
    output_tokens = _number(record.get("output_tokens"))
    cached_tokens = _number(record.get("cached_tokens"))
    if input_tokens is None or output_tokens is None or cached_tokens is None:
        return None
    return (
        input_tokens * prices["input"]
        + output_tokens * prices["output"]
        + cached_tokens * prices["cached_input"]
    ) / 1_000_000


def _stage_record(record: Any) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    model = _model(record.get("model"))
    cost = _number(record.get("cost_usd"))
    if cost is None:
        cost = _cost_from_usage(model, record)
    if cost is None:
        return None
    return {
        "model": model,
        "input_tokens": _integer(record.get("input_tokens")),
        "output_tokens": _integer(record.get("output_tokens")),
        "cached_tokens": _integer(record.get("cached_tokens")),
        "calls": _integer(record.get("calls")),
        "cost_usd": cost,
    }


def _index_items(source: Path, warning: TextIO) -> list[Any]:
    value = _read_json(source / "index.json", warning)
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        _warning(warning, f"{source / 'index.json'} has no object items array")
        return []
    return value["items"]


def _video_row(
    source: Path,
    item: dict[str, Any],
    warning: TextIO,
) -> dict[str, Any] | None:
    video_id = item.get("video_id")
    if not isinstance(video_id, str) or not video_id:
        _warning(warning, "analyzed index item has no video_id; skipping")
        return None
    path = source / "processed" / f"{video_id}.json"
    if not path.is_file():
        _warning(warning, f"missing processed record {path}; skipping")
        return None
    value = _read_json(path, warning)
    if not isinstance(value, dict):
        return None

    costs = value.get("costs")
    if not isinstance(costs, dict):
        _warning(warning, f"{path} has no costs object; skipping cost record")
        return None
    stages = {
        stage: parsed
        for stage in STAGES
        if (parsed := _stage_record(costs.get(stage))) is not None
    }
    if not stages:
        _warning(warning, f"{path} has no usable cost stages; skipping cost record")
        return None

    metadata = value.get("metadata")
    duration_s = _parse_duration(metadata.get("duration") if isinstance(metadata, dict) else None)
    if duration_s is None:
        _warning(warning, f"{path} has no parseable metadata duration; using unknown bucket")

    def text(name: str) -> str | None:
        first = item.get(name)
        if isinstance(first, str):
            return first
        second = value.get(name)
        return second if isinstance(second, str) else None

    published_at = text("published_at")
    ingested_at = text("ingested_at")
    recorded_at = next(
        (
            stage.get("recorded_at")
            for stage in (costs.get(name) for name in STAGES)
            if isinstance(stage, dict) and isinstance(stage.get("recorded_at"), str)
        ),
        None,
    )
    total = sum(stage["cost_usd"] for stage in stages.values())
    return {
        "video_id": video_id,
        "title": text("title") or video_id,
        "channel": text("channel") or "",
        "published_at": published_at,
        "ingested_at": ingested_at,
        "recorded_at": recorded_at,
        "duration_s": duration_s,
        "bucket": _duration_bucket(duration_s),
        "stages": stages,
        "total_usd": total,
    }


def _source_summary(items: list[Any], rows: list[dict[str, Any]]) -> dict[str, int]:
    dictionaries = [item for item in items if isinstance(item, dict)]
    return {
        "index_items": len(items),
        "analyzed": sum(item.get("status") == "analyzed" for item in dictionaries),
        "skipped": sum(item.get("status") == "skipped" for item in dictionaries),
        "failed": sum(item.get("status") == "failed" for item in dictionaries),
        "with_cost_records": len(rows),
    }


def _stage_summaries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for stage in STAGES:
        stage_rows = [row["stages"][stage] for row in rows if stage in row["stages"]]
        total = sum(row["cost_usd"] for row in stage_rows)
        models: dict[str, int] = defaultdict(int)
        for row in stage_rows:
            models[row["model"]] += 1
        result.append(
            {
                "stage": stage,
                "videos": len(stage_rows),
                "total_usd": _round(total),
                "avg_usd": _round(total / len(stage_rows)) if stage_rows else 0.0,
                "models": {model: models[model] for model in sorted(models)},
            }
        )
    return result


def _duration_s_value(value: float | None) -> int | float | None:
    if value is None or not value.is_integer():
        return value
    return int(value)


def _rates() -> list[dict[str, Any]]:
    return [
        {
            "model": model,
            "input_per_1m": values["input"],
            "output_per_1m": values["output"],
            "cached_input_per_1m": values["cached_input"],
        }
        for model, values in PRICE_USD_PER_1M.items()
    ]


def _gates_report(root: Path, warning: TextIO) -> dict[str, Any]:
    if root.name == "recommendations":
        recommendation_root = root
    elif (root / "recommendations").is_dir():
        recommendation_root = root / "recommendations"
    else:
        recommendation_root = root
    rows: list[tuple[str | None, float]] = []
    if recommendation_root.is_dir():
        paths = sorted(recommendation_root.rglob("*.json"))
    else:
        paths = []
    for path in paths:
        value = _read_json(path, warning)
        if not isinstance(value, dict):
            continue
        meta = value.get("meta")
        if not isinstance(meta, dict):
            continue
        cost = _number(meta.get("cost_usd"))
        if cost is None:
            continue
        rows.append((_month(meta.get("scored_at")), cost))

    by_month: dict[str, dict[str, Any]] = {}
    for month, cost in rows:
        key = month or "unknown"
        current = by_month.setdefault(key, {"scored": 0, "total_raw": 0.0})
        current["scored"] += 1
        current["total_raw"] += cost
    total = sum(cost for _, cost in rows)
    return {
        "scored": len(rows),
        "total_usd": _round(total),
        "avg_usd": _round(total / len(rows)) if rows else 0.0,
        "by_month": {
            month: {
                "scored": value["scored"],
                "total_usd": _round(value["total_raw"]),
            }
            for month, value in sorted(by_month.items())
        },
    }


def _operations_cost(operations: dict[str, Any] | None) -> float | None:
    if operations is None:
        return None
    return _number(operations.get("agent_ops_usd"))


def build_cost_report(
    source: str | Path,
    *,
    gates: str | Path | None = None,
    operations: dict[str, Any] | None = None,
    generated_at: str | None = None,
    warning: TextIO | None = None,
) -> dict[str, Any]:
    """Build the schema-versioned cost report."""

    warning = warning or sys.stderr
    source = Path(source).expanduser().resolve()
    items = _index_items(source, warning)
    analyzed = [
        item
        for item in items
        if isinstance(item, dict) and item.get("status") == "analyzed"
    ]
    rows = [
        row
        for item in analyzed
        if (row := _video_row(source, item, warning)) is not None
    ]

    processing = sum(row["total_usd"] for row in rows)
    gate_report = _gates_report(Path(gates).expanduser().resolve(), warning) if gates else None
    scoring = gate_report["total_usd"] if gate_report is not None else None
    ops_cost = _operations_cost(operations)

    timestamp = generated_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    totals = {
        "processing_usd": _round(processing),
        "scoring_usd": scoring,
        "agent_ops_usd": _round(ops_cost) if ops_cost is not None else None,
    }

    bucket_rows: dict[str, list[dict[str, Any]]] = {bucket: [] for bucket in DURATION_BUCKETS}
    for row in rows:
        bucket_rows[row["bucket"]].append(row)
    duration_buckets = []
    for bucket in DURATION_BUCKETS:
        bucket_values = bucket_rows[bucket]
        total = sum(row["total_usd"] for row in bucket_values)
        duration_buckets.append(
            {
                "label": bucket,
                "videos": len(bucket_values),
                "total_usd": _round(total),
                "avg_usd": _round(total / len(bucket_values)) if bucket_values else 0.0,
            }
        )

    monthly_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        month = (
            _month(row["ingested_at"])
            or _month(row["recorded_at"])
            or _month(row["published_at"])
            or "unknown"
        )
        monthly_rows[month].append(row)
    monthly = []
    for month in sorted(monthly_rows):
        month_values = monthly_rows[month]
        total = sum(row["total_usd"] for row in month_values)
        monthly.append(
            {
                "month": month,
                "videos": len(month_values),
                "total_usd": _round(total),
                "avg_usd": _round(total / len(month_values)),
            }
        )

    distribution_values = [row["total_usd"] for row in rows]
    distribution = {
        "median_usd": _round(_percentile(distribution_values, 0.50)),
        "p90_usd": _round(_percentile(distribution_values, 0.90)),
        "p95_usd": _round(_percentile(distribution_values, 0.95)),
        "p99_usd": _round(_percentile(distribution_values, 0.99)),
        "max_usd": _round(max(distribution_values, default=0.0)),
    }

    per_video = []
    for row in sorted(rows, key=lambda value: (-value["total_usd"], value["video_id"])):
        per_video.append(
            {
                "video_id": row["video_id"],
                "title": row["title"],
                "channel": row["channel"],
                "published_at": row["published_at"],
                "duration_s": _duration_s_value(row["duration_s"]),
                "bucket": row["bucket"],
                "total_usd": _round(row["total_usd"]),
                **{
                    stage: (
                        {
                            "model": value["model"],
                            "input_tokens": value["input_tokens"],
                            "output_tokens": value["output_tokens"],
                            "cached_tokens": value["cached_tokens"],
                            "calls": value["calls"],
                            "cost_usd": _round(value["cost_usd"]),
                        }
                        if (value := row["stages"].get(stage)) is not None
                        else None
                    )
                    for stage in STAGES
                },
            }
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": timestamp,
        "currency": "USD",
        "source": _source_summary(items, rows),
        "totals": totals,
        "stages": _stage_summaries(rows),
        "gates": gate_report,
        "distribution": distribution,
        "duration_buckets": duration_buckets,
        "monthly": monthly,
        "rates": _rates(),
    }
    if operations is not None:
        report["operations"] = operations.get("operations", [])
    report["per_video"] = per_video
    return report


def _usd(value: Any, digits: int = 2) -> str:
    number = _number(value)
    return "—" if number is None else f"${number:.{digits}f}"


def _percent(value: float) -> str:
    return f"{round(value):.0f}%"


def _models_label(models: dict[str, int]) -> str:
    return ", ".join(
        model if count == 1 else f"{model} ({count})"
        for model, count in models.items()
    ) or "—"


def render_costs_markdown(report: dict[str, Any]) -> str:
    """Render a human-facing Markdown cost breakdown."""

    generated_at = str(report.get("generated_at", ""))
    data_as_of = generated_at.split("T", 1)[0]
    source = report["source"]
    totals = report["totals"]
    processing = _number(totals.get("processing_usd")) or 0.0
    scoring = _number(totals.get("scoring_usd")) or 0.0
    agent_ops = _number(totals.get("agent_ops_usd")) or 0.0
    corpus_total = processing + scoring + agent_ops
    lines = [
        "<!-- generated by costs-report; do not edit by hand -->",
        "",
        f"Data as of **{data_as_of}**.",
        "",
        f"Scope: the full pipeline for **{source['analyzed']} analyzed videos** "
        f"({source['index_items']} index items; {source['skipped']} skipped, "
        f"{source['failed']} failed, {source['with_cost_records']} with cost records).",
        "",
        "## Headline",
        "",
        "| Scope | USD |",
        "|---|---:|",
        f"| Per-video processing to date "
        f"({source['with_cost_records']} videos with cost records) | "
        f"{_usd(totals['processing_usd'])} |",
    ]
    if totals.get("scoring_usd") is not None:
        lines.append(f"| Relevance scoring to date | {_usd(totals['scoring_usd'])} |")
    if totals.get("agent_ops_usd") is not None:
        lines.append(f"| LLM agent-run overhead | {_usd(totals['agent_ops_usd'])} |")
    lines.extend([f"| **Corpus total to date** | **{_usd(corpus_total)}** |", ""])

    lines.extend(
        [
            "## Per-video processing",
            "",
            "| Stage | Model | Volume | Total | Average |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for stage in report["stages"]:
        lines.append(
            f"| {stage['stage'].title()} | {_models_label(stage['models'])} | "
            f"{stage['videos']} | {_usd(stage['total_usd'])} | {_usd(stage['avg_usd'], 3)} |"
        )
    total_average = (
        _usd(processing / source["with_cost_records"], 3)
        if source["with_cost_records"]
        else "—"
    )
    lines.append(
        f"| **Total per video** | | | {_usd(totals['processing_usd'])} | {total_average} |"
    )
    lines.extend(
        [
            "",
            "### Duration buckets",
            "",
            "| Duration | Videos | Average | Total | Spend share |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for bucket in report["duration_buckets"]:
        share = (
            100 * (_number(bucket["total_usd"]) or 0.0) / processing
            if processing
            else 0.0
        )
        lines.append(
            f"| {bucket['label']} | {bucket['videos']} | {_usd(bucket['avg_usd'], 3)} | "
            f"{_usd(bucket['total_usd'])} | {_percent(share)} |"
        )
    distribution = report["distribution"]
    lines.extend(
        [
            "",
            f"Distribution across the {source['with_cost_records']} costed videos: "
            f"median {_usd(distribution['median_usd'])}, p90 {_usd(distribution['p90_usd'])}, "
            f"p95 {_usd(distribution['p95_usd'])}, p99 {_usd(distribution['p99_usd'])}, "
            f"max {_usd(distribution['max_usd'])}.",
        ]
    )

    if report.get("gates") is not None:
        gates = report["gates"]
        lines.extend(
            [
                "",
                "## Relevance scoring",
                "",
                "| Stage | Volume | Total | Average |",
                "|---|---:|---:|---:|",
                f"| Relevance gate | {gates['scored']} | {_usd(gates['total_usd'])} | "
                f"{_usd(gates['avg_usd'], 4)} |",
                "",
                "Monthly split:",
                "",
                "| Month | Scored | Spend |",
                "|---|---:|---:|",
            ]
        )
        for month, value in gates["by_month"].items():
            lines.append(f"| {month} | {value['scored']} | {_usd(value['total_usd'])} |")

    lines.extend(
        [
            "",
            "## Monthly history",
            "",
            "| Month | Videos analyzed | Spend | Average |",
            "|---|---:|---:|---:|",
        ]
    )
    for row in report["monthly"]:
        lines.append(
            f"| {row['month']} | {row['videos']} | {_usd(row['total_usd'])} | "
            f"{_usd(row['avg_usd'], 3)} |"
        )

    if report.get("operations") is not None:
        lines.extend(
            [
                "",
                "## Operations layer",
                "",
                "| Component | Runs as | Cost |",
                "|---|---|---:|",
            ]
        )
        for operation in report["operations"]:
            if not isinstance(operation, dict):
                continue
            lines.append(
                f"| {operation.get('component', '—')} | {operation.get('runs_as', '—')} | "
                f"{operation.get('cost', '—')} |"
            )

    lines.extend(
        [
            "",
            "## Rates used",
            "",
            "| Model | Input $/1M | Output $/1M | Cached $/1M |",
            "|---|---:|---:|---:|",
        ]
    )
    for rate in report["rates"]:
        lines.append(
            f"| {rate['model']} | {rate['input_per_1m']:.3f} | "
            f"{rate['output_per_1m']:.3f} | {rate['cached_input_per_1m']:.3f} |"
        )

    lines.extend(
        [
            "",
            "## Caveats",
            "",
            "- Failed attempts are not fully priced: some error paths record no token",
            "  usage, so retried work adds a small unpriced margin (observed during",
            "  development: one error event consumed ~190K input tokens with no usage",
            "  recorded).",
            "- Prices are snapshots of provider pricing pages and drift over time; records",
            "  are priced with the table current at recording time.",
            "- Agent-run overhead is the charged USD per run and includes provider-side",
            "  retries.",
            "",
            "## Provenance",
            "",
            "- Per video: `processed/<video_id>.json` → `costs.summarize` and",
            "  `costs.analyze`, each `{model, input_tokens, output_tokens, cached_tokens,",
            "  calls, duration_s, recorded_at, cost_usd}`.",
            "- Per scored candidate: `recommendations/<channel>/<video_id>.json` →",
            "  `meta.cost_usd` with usage.",
            "- Per cron run: the run output carries a `$… USD · model · N API calls ·",
            "  tokens` line.",
            "- Corpus rollup: `index.json` items carry `cost_usd_total`; the site's",
            "  `data/corpus.json` exposes computed counts.",
        ]
    )
    return "\n".join(lines) + "\n"


# Public aliases keep the small module convenient for callers and tests.
generate_cost_report = build_cost_report
render_markdown = render_costs_markdown
