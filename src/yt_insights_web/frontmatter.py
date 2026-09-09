"""OKF frontmatter and safe Markdown parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml
from markdown_it import MarkdownIt


class FrontMatterError(ValueError):
    """Raised when a summary does not have valid YAML frontmatter."""


@dataclass(frozen=True)
class ParsedSummary:
    metadata: dict[str, Any]
    markdown: str
    html: str


_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(?P<yaml>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_MARKDOWN = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})


def parse_summary(text: str) -> ParsedSummary:
    """Parse an OKF summary, rejecting malformed delimiters and YAML roots."""

    match = _FRONTMATTER.match(text)
    if match is None:
        raise FrontMatterError("summary is missing YAML frontmatter delimiters")
    try:
        metadata = yaml.safe_load(match.group("yaml"))
    except yaml.YAMLError as exc:
        raise FrontMatterError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(metadata, dict):
        raise FrontMatterError("frontmatter root must be a mapping")
    markdown = text[match.end() :].lstrip("\r\n")
    return ParsedSummary(metadata=dict(metadata), markdown=markdown, html=render_markdown(markdown))


def render_markdown(markdown: str) -> str:
    """Render Markdown with raw HTML disabled."""

    return _MARKDOWN.render(markdown)
