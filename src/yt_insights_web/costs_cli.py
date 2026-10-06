"""Command-line entry point for ``costs-report``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .costs import build_cost_report, render_costs_markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="costs-report",
        description="Generate deterministic cost evidence from a yt-insights checkout.",
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument(
        "--gates",
        type=Path,
        help="optional directory containing recommendations/**/*.json",
    )
    parser.add_argument("--json-out", required=True, type=Path)
    parser.add_argument("--md-out", type=Path)
    parser.add_argument("--ops", type=Path, help="optional operations JSON file")
    return parser


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _load_operations(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("operations JSON must be an object")
    amount = value.get("agent_ops_usd")
    if amount is not None and (
        isinstance(amount, bool) or not isinstance(amount, (int, float))
    ):
        raise ValueError("agent_ops_usd must be a number or null")
    if not isinstance(value.get("operations"), list):
        raise ValueError("operations JSON must contain an operations list")
    return value


def _summary(report: dict[str, Any]) -> str:
    source = report["source"]
    totals = report["totals"]
    result = (
        f"analyzed {source['analyzed']}, with_cost_records {source['with_cost_records']}, "
        f"processing_usd {totals['processing_usd']:.6f}"
    )
    if report["gates"] is not None:
        result += (
            f", gates scored {report['gates']['scored']}, "
            f"scoring_usd {report['gates']['total_usd']:.6f}"
        )
    return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = args.source.expanduser().resolve()
    if not source.is_dir():
        print(f"error: source directory does not exist: {args.source}", file=sys.stderr)
        return 2
    for output in (args.json_out, args.md_out):
        if output is not None and _inside(output, source):
            print("error: output path overlaps source", file=sys.stderr)
            return 2
    if args.gates is not None and _inside(args.gates, source):
        print("error: gates path overlaps source", file=sys.stderr)
        return 2

    try:
        operations = _load_operations(args.ops) if args.ops is not None else None
        report = build_cost_report(source, gates=args.gates, operations=operations)
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        if args.md_out is not None:
            args.md_out.parent.mkdir(parents=True, exist_ok=True)
            args.md_out.write_text(
                render_costs_markdown(report),
                encoding="utf-8",
                newline="\n",
            )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
