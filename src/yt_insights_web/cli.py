"""Command-line entry point for the static site builder."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .build import BuildError, build_site


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

    try:
        build_site(
            source,
            args.out,
            site_title=args.site_title,
            base_path=args.base_path,
            publication_mode=args.publication,
            acknowledge_private_unreviewed=args.acknowledge_private_unreviewed,
            generated_at=args.generated_at,
        )
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"built {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
