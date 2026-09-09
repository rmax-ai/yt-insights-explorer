"""Stable, path-safe names for generated resources."""

from __future__ import annotations

import hashlib
import re
import unicodedata


def canonical_text(value: str) -> str:
    """Collapse whitespace and use Unicode casefold for identity."""

    return " ".join(value.split()).casefold()


def slugify(value: str) -> str:
    """Convert arbitrary Unicode text into a conservative ASCII slug."""

    normalized = unicodedata.normalize("NFKD", value)
    without_marks = "".join(char for char in normalized if not unicodedata.combining(char))
    ascii_text = without_marks.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return slug or "item"


def item_id(video_id: str, section: str, source_index: int) -> str:
    return f"{video_id}:{section}:{source_index}"


def video_slug(title: str, video_id: str) -> str:
    return f"{slugify(title)}-{slugify(video_id)}"


def concept_id(name: str) -> str:
    canonical = canonical_text(name)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]
    return f"c-{slugify(canonical)}-{digest}"


def concept_slug(name: str) -> str:
    canonical = canonical_text(name)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]
    return f"{slugify(canonical)}-{digest}"


def edge_id(source_id: str, target_id: str) -> str:
    low, high = sorted((source_id, target_id))
    digest = hashlib.sha256(f"{low}\n{high}".encode()).hexdigest()[:16]
    return f"e-{digest}"
