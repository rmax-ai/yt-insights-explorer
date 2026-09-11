"""Integrity checks for a generated static site."""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

# Self-imposed sanity cap; GitHub Pages allows up to 1 GB per repository.
# Raised from 25 MB on 2026-09-10 as the corpus outgrew the old budget.
MAX_BYTES = 100_000_000
_WINDOWS_PATH = re.compile(r"\b[A-Za-z]:[\\/]")


class VerificationError(RuntimeError):
    """Raised when a generated site violates the static-site contract."""


@dataclass(frozen=True)
class VerificationReport:
    html_files: int
    video_count: int
    total_bytes: int


class _HTMLCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[tuple[str, str]] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = dict(attrs)
        if "id" in attributes and attributes["id"]:
            self.ids.add(attributes["id"])
        for attribute in ("href", "src"):
            value = attributes.get(attribute)
            if value:
                self.references.append((attribute, value))

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)


def _index_tree(
    root: Path,
) -> tuple[int, set[str], set[str], list[Path], list[Path]]:
    """Walk the generated tree once and collect paths needed by validation."""

    total_bytes = 0
    file_paths: set[str] = set()
    directory_paths: set[str] = {""}
    html_paths: list[Path] = []
    leak_paths: list[Path] = []
    # Generated trees contain no symlinks, so Path.walk's directory/file split
    # is sufficient without resolving or stat-ing every path for classification.
    for directory, dirnames, filenames in root.walk():
        directory_relative = directory.relative_to(root).as_posix()
        if directory_relative == ".":
            directory_relative = ""
        directory_paths.add(directory_relative)
        directory_paths.update(
            posixpath.join(directory_relative, dirname) if directory_relative else dirname
            for dirname in dirnames
        )
        for filename in filenames:
            path = directory / filename
            relative = path.relative_to(root).as_posix()
            file_paths.add(relative)
            total_bytes += path.stat().st_size
            if path.suffix == ".html":
                html_paths.append(path)
            if path.suffix.lower() != ".html":
                leak_paths.append(path)
    return total_bytes, file_paths, directory_paths, html_paths, leak_paths


def _filesystem_leak(text: str) -> bool:
    return "/home/" in text or "file:" in text or bool(_WINDOWS_PATH.search(text))


def _load_site_config(root: Path) -> str:
    path = root / "data" / "corpus.json"
    if not path.is_file():
        raise VerificationError("missing data/corpus.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot read data/corpus.json: {exc}") from exc
    base_path = data.get("site", {}).get("base_path", "./")
    if base_path in {None, "", ".", "./"}:
        return "./"
    return str(base_path).rstrip("/") + "/"


def _local_target(
    root: Path,
    page_relative: str,
    raw_path: str,
    base_path: str,
    directory_paths: set[str],
) -> tuple[Path, str]:
    path = unquote(raw_path)
    if path.startswith("/"):
        if base_path != "./" and path.startswith(base_path):
            path = path[len(base_path) :]
        else:
            path = path.lstrip("/")
        relative = posixpath.normpath(path)
    else:
        page_parent = posixpath.dirname(page_relative)
        relative = posixpath.normpath(posixpath.join(page_parent, path))
    if relative == ".." or relative.startswith("../"):
        raise VerificationError(
            f"internal link escapes generated tree: {root / page_relative}: {raw_path}"
        )
    if relative == ".":
        relative = ""
    if relative in directory_paths:
        relative = f"{relative}/index.html" if relative else "index.html"
    return root / relative, relative


def _check_reference(
    root: Path,
    page: Path,
    attribute: str,
    value: str,
    page_collectors: dict[Path, _HTMLCollector],
    page_relative: str,
    base_path: str,
    allowed_video_ids: set[str],
    file_paths: set[str],
    directory_paths: set[str],
) -> None:
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme != "https" or parsed.netloc != "www.youtube.com":
            raise VerificationError(f"forbidden external URL in {page}: {value}")
        if parsed.path != "/watch" or not parse_qs(parsed.query).get("v", [None])[0]:
            raise VerificationError(f"external URL is not a YouTube watch URL in {page}: {value}")
        if parse_qs(parsed.query)["v"][0] not in allowed_video_ids:
            raise VerificationError(
                f"external URL is not derived from this corpus in {page}: {value}"
            )
        return
    if value.startswith("//") or value.startswith("javascript:") or value.startswith("data:"):
        raise VerificationError(f"forbidden URL in {page}: {value}")
    raw_path = parsed.path
    if not raw_path:
        target = page
        target_relative = page_relative
    else:
        target, target_relative = _local_target(
            root,
            page_relative,
            raw_path,
            base_path,
            directory_paths,
        )
    if target_relative not in file_paths:
        raise VerificationError(f"missing local target in {page}: {value}")
    if parsed.fragment:
        if target.suffix.lower() != ".html":
            raise VerificationError(f"fragment targets a non-HTML file in {page}: {value}")
        collector = page_collectors.get(target)
        if collector is None or unquote(parsed.fragment) not in collector.ids:
            raise VerificationError(f"missing fragment in {page}: {value}")


def _check_video_provenance(root: Path) -> tuple[int, set[str]]:
    corpus_path = root / "data" / "corpus.json"
    data = json.loads(corpus_path.read_text(encoding="utf-8"))
    video_ids = data.get("video_ids")
    if not isinstance(video_ids, list):
        raise VerificationError("data/corpus.json is missing video_ids")
    count = 0
    allowed_video_ids: set[str] = set()
    for video_id in video_ids:
        json_path = root / "data" / "videos" / f"{video_id}.json"
        if not json_path.is_file():
            raise VerificationError(f"missing video JSON for {video_id}")
        try:
            video = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VerificationError(f"cannot read video JSON for {video_id}: {exc}") from exc
        url = video.get("url")
        if not isinstance(url, str):
            raise VerificationError(f"video JSON has no local URL for {video_id}")
        html_path = root / url
        if not html_path.is_file():
            raise VerificationError(f"missing video HTML for {video_id}: {url}")
        source_uri = video.get("source", {}).get("uri")
        parsed_source = urlsplit(source_uri or "")
        source_id = parse_qs(parsed_source.query).get("v", [None])[0]
        if (
            parsed_source.netloc != "www.youtube.com"
            or parsed_source.path != "/watch"
            or source_id != video_id
        ):
            raise VerificationError(f"video JSON has an invalid source URL for {video_id}")
        allowed_video_ids.add(video_id)
        count += 1
    return count, allowed_video_ids


def verify_site(root: str | Path) -> VerificationReport:
    """Validate links, fragments, provenance, path leakage, and size."""

    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise VerificationError(f"generated site directory does not exist: {root}")
    total_bytes, file_paths, directory_paths, html_paths, leak_paths = _index_tree(root)
    if total_bytes > MAX_BYTES:
        raise VerificationError(
            f"generated tree is over {MAX_BYTES // 1_000_000} MB: {total_bytes} bytes"
        )
    base_path = _load_site_config(root)
    html_paths.sort()
    collectors: dict[Path, _HTMLCollector] = {}
    page_relatives: dict[Path, str] = {}
    for page in html_paths:
        try:
            text = page.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise VerificationError(f"cannot read HTML page {page}: {exc}") from exc
        if _filesystem_leak(text):
            raise VerificationError(f"filesystem path leak in {page}")
        collector = _HTMLCollector()
        try:
            collector.feed(text)
            collector.close()
        except Exception as exc:
            raise VerificationError(f"invalid HTML in {page}: {exc}") from exc
        collectors[page] = collector
        page_relatives[page] = page.relative_to(root).as_posix()
    for path in leak_paths:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if _filesystem_leak(text):
            raise VerificationError(f"filesystem path leak in {path}")
    video_count, allowed_video_ids = _check_video_provenance(root)
    for page, collector in collectors.items():
        for attribute, value in collector.references:
            _check_reference(
                root,
                page,
                attribute,
                value,
                collectors,
                page_relatives[page],
                base_path,
                allowed_video_ids,
                file_paths,
                directory_paths,
            )
    return VerificationReport(len(html_paths), video_count, total_bytes)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Verify a generated YT Insights site.")
    parser.add_argument("root", type=Path)
    args = parser.parse_args(argv)
    try:
        report = verify_site(args.root)
    except VerificationError as exc:
        print(f"fail: {exc}")
        return 1
    print(
        f"pass: {report.html_files} HTML files, {report.video_count} videos, "
        f"{report.total_bytes} bytes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
