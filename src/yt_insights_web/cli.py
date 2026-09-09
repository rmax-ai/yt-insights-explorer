"""Command-line entry point for the static site builder."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build_site",
        description="Build a deterministic static YT Insights Explorer site.",
    )
    parser.add_argument(
        "--source",
        required=True,
        type=Path,
        help="read-only yt-insights source checkout",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("site"),
        help="output directory (default: site)",
    )
    parser.add_argument(
        "--base-path",
        default="./",
        help='deployment prefix, for example "/yt-insights/"',
    )
    parser.add_argument(
        "--publication",
        choices=("private", "public"),
        default="private",
        help="publication treatment (default: private)",
    )
    parser.add_argument(
        "--acknowledge-private-unreviewed",
        action="store_true",
        help="allow public output while records remain private or unreviewed",
    )
    parser.add_argument(
        "--site-title",
        default="YT Insights Explorer",
        help="site title (default: YT Insights Explorer)",
    )
    parser.add_argument(
        "--generated-at",
        help="optional ISO-8601 display metadata",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = args.source.expanduser().resolve()
    if not source.is_dir():
        print(f"error: source directory does not exist: {args.source}", file=sys.stderr)
        return 2
    index_path = source / "index.json"
    if not index_path.is_file():
        print(f"error: source is missing index.json: {source}", file=sys.stderr)
        return 2

    # Full loading and rendering are added in later build tasks. Keeping this
    # skeleton side-effect small makes the initial package usable while the
    # source contracts are implemented.
    args.out.expanduser().mkdir(parents=True, exist_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
