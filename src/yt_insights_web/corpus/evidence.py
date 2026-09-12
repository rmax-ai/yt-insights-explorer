"""Deterministic V1 evidence adaptation and Markdown quote recovery.

The persisted summary quote syntax owned by this module is exactly:

``> "TEXT" (at M:SS)``

The line must start with ``> ``, use the literal ``"`` delimiters, include
the literal ``(at `` marker, use a one-or-more-digit minute and a two-digit
second from ``00`` through ``59``, and end with ``)``.  Only that exact
timestamped form recovers a timestamp.  A bare line of the form
``> "TEXT"`` is retained as an untimed quote; all other lines are ignored.
Matching is byte-for-byte on the decoded Python strings.  No case folding,
whitespace collapsing, punctuation removal, substring matching, or fuzzy
matching is performed.

The functions here are pure: they only inspect their arguments and return
immutable values.  They do not read files, consult the clock, or access a
network.
"""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from typing import NamedTuple
from urllib.parse import quote, urlsplit, urlunsplit

from .identity import evidence_content_id
from .normalized_models import (
    Evidence,
    EvidenceAvailability,
    EvidenceOccurrence,
    Provenance,
)
from .source_models import EvidenceQuote

SUMMARY_QUOTE_TIMESTAMP_METHOD = "summary_quote_exact"


class MarkdownQuote(NamedTuple):
    """One quote parsed from the summary's rendered Markdown."""

    text: str
    timestamp_seconds: int | None


SummaryQuote = MarkdownQuote
OccurrenceReference = EvidenceOccurrence
EvidenceOccurrenceReference = EvidenceOccurrence


_TIMESTAMPED_QUOTE = (
    r'^> "(?P<text>[^"]+)" \(at (?P<minutes>[0-9]+):(?P<seconds>[0-5][0-9])\)$'
)
_UNTIMED_QUOTE = r'^> "(?P<text>[^"]+)"$'


def parse_markdown_quote_line(line: str) -> MarkdownQuote | None:
    """Parse one exact rendered quote line, or return ``None``.

    Timestamped lines are intentionally matched with a full regular
    expression.  Markdown blockquotes with indentation, alternate quote
    markers, one-digit seconds, trailing text, or other producer formats do
    not recover a timestamp.
    """

    import re

    if not isinstance(line, str):
        raise TypeError("line must be a string")
    line = line.rstrip("\r\n")
    timestamped = re.fullmatch(_TIMESTAMPED_QUOTE, line)
    if timestamped is not None:
        return MarkdownQuote(
            text=timestamped.group("text"),
            timestamp_seconds=int(timestamped.group("minutes")) * 60
            + int(timestamped.group("seconds")),
        )
    untimed = re.fullmatch(_UNTIMED_QUOTE, line)
    if untimed is not None:
        return MarkdownQuote(text=untimed.group("text"), timestamp_seconds=None)
    return None


def parse_markdown_quotes(markdown: str) -> tuple[MarkdownQuote, ...]:
    """Return all producer-shaped quote lines in source order."""

    if not isinstance(markdown, str):
        raise TypeError("markdown must be a string")
    return tuple(
        quote_line
        for line in markdown.splitlines()
        if (quote_line := parse_markdown_quote_line(line)) is not None
    )


parse_summary_quotes = parse_markdown_quotes
parse_quotes = parse_markdown_quotes
parse_quote_line = parse_markdown_quote_line
parse_markdown_quote = parse_markdown_quote_line


def unique_exact_summary_quote(text: str, summary_markdown: str) -> MarkdownQuote | None:
    """Return a uniquely matching summary quote, including an untimed match.

    A unique untimed match is returned so callers can distinguish it from no
    match, but it does not recover a timestamp.  A zero-match or
    multiple-match result is ``None``.
    """

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    matches = tuple(
        quote_line
        for quote_line in parse_markdown_quotes(summary_markdown)
        if quote_line.text == text
    )
    return matches[0] if len(matches) == 1 else None


def recover_summary_timestamp(text: str, summary_markdown: str) -> int | None:
    """Recover a timestamp only from one exact, timestamped summary quote."""

    match = unique_exact_summary_quote(text, summary_markdown)
    return match.timestamp_seconds if match is not None else None


recover_timestamp = recover_summary_timestamp
recover_timestamp_from_summary = recover_summary_timestamp


def _format_seconds(seconds: int | float) -> str:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise TypeError("timestamp_seconds must be a number")
    if not isfinite(float(seconds)) or seconds < 0:
        raise ValueError("timestamp_seconds must be finite and non-negative")
    return str(int(seconds)) if float(seconds).is_integer() else str(seconds)


def youtube_source_url(
    source_uri: str | None,
    video_id: str,
    timestamp_seconds: int | float | None = None,
) -> str | None:
    """Derive a base YouTube URL or its deterministic timestamp deep link."""

    if source_uri is None:
        return None
    if not isinstance(source_uri, str) or not source_uri:
        raise TypeError("source_uri must be a non-empty string or None")
    if not isinstance(video_id, str) or not video_id:
        raise TypeError("video_id must be a non-empty string")

    parsed = urlsplit(source_uri)
    if parsed.scheme and parsed.netloc and parsed.path:
        base = urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                f"v={quote(video_id, safe='')}",
                "",
            )
        )
    else:
        base = source_uri.split("&t=", 1)[0]
    if timestamp_seconds is None:
        return base
    return f"{base}&t={_format_seconds(timestamp_seconds)}s"


def _quote_values(
    quote_value: str | EvidenceQuote | Mapping[str, object],
) -> tuple[str, int | float | None, str | None]:
    if isinstance(quote_value, str):
        return quote_value, None, None
    if isinstance(quote_value, Mapping):
        quote_value = EvidenceQuote.from_value(quote_value, "evidence_quote")
    if isinstance(quote_value, EvidenceQuote):
        return quote_value.text, quote_value.timestamp_seconds, quote_value.source_url
    raise TypeError("quote_value must be a string or EvidenceQuote")


def evidence_from_quote(
    quote_value: str | EvidenceQuote | Mapping[str, object],
    *,
    video_id: str,
    section: str,
    item_index: int,
    quote_index: int,
    summary_markdown: str,
    source_uri: str | None,
    source_version: int = 1,
    location: str | None = None,
) -> Evidence:
    """Build one immutable evidence object from a V1 quote slot.

    Existing timestamped quote-object metadata is preserved when summary
    recovery has no accepted result.  Automatic recovery, when present, takes
    precedence and is the only path that assigns
    ``timestamp_method="summary_quote_exact"``.
    """

    text, persisted_timestamp, persisted_source_url = _quote_values(quote_value)
    occurrence = EvidenceOccurrence(section, item_index, quote_index)
    provenance = Provenance(
        source_version=source_version,
        location=location or f"insights.json::{section}[{item_index}]",
        source_index=item_index,
    )
    match = unique_exact_summary_quote(text, summary_markdown)
    if match is not None and match.timestamp_seconds is not None:
        timestamp = match.timestamp_seconds
        timestamp_method = SUMMARY_QUOTE_TIMESTAMP_METHOD
    else:
        timestamp = persisted_timestamp
        timestamp_method = None

    base_url = youtube_source_url(source_uri, video_id)
    if timestamp is not None:
        derived_url = youtube_source_url(source_uri, video_id, timestamp)
    else:
        derived_url = base_url or persisted_source_url
    if timestamp_method is not None:
        availability = EvidenceAvailability.RESOLVED_ENRICHMENT
    elif timestamp is not None or derived_url is not None:
        availability = EvidenceAvailability.RESOLVED
    else:
        availability = EvidenceAvailability.UNAVAILABLE

    return Evidence(
        text=text,
        availability=availability,
        provenance=provenance,
        timestamp_seconds=timestamp,
        source_url=derived_url,
        resolution_method=timestamp_method,
        content_id=evidence_content_id(video_id, text),
        occurrence=occurrence,
        timestamp_method=timestamp_method,
    )


build_evidence = evidence_from_quote
content_id = evidence_content_id
evidence_id = evidence_content_id


__all__ = [
    "MarkdownQuote",
    "OccurrenceReference",
    "EvidenceOccurrenceReference",
    "SUMMARY_QUOTE_TIMESTAMP_METHOD",
    "SummaryQuote",
    "build_evidence",
    "content_id",
    "evidence_id",
    "evidence_from_quote",
    "parse_markdown_quote",
    "parse_markdown_quote_line",
    "parse_markdown_quotes",
    "parse_quotes",
    "parse_quote_line",
    "parse_summary_quotes",
    "recover_summary_timestamp",
    "recover_timestamp",
    "recover_timestamp_from_summary",
    "unique_exact_summary_quote",
    "youtube_source_url",
]
