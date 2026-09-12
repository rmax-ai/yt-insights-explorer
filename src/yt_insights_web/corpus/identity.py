"""Pure, versioned identities for adapted V1 corpus values.

The two fingerprint algorithms in this module are deliberately separate:

``item-fingerprint-v1``
    ``SHA-256(UTF-8("item-fingerprint-v1\\0" + video_id + "\\0" +
    section + "\\0" + source_index + "\\0" + primary_text))``.
    ``source_index`` is its base-10 representation, or the empty string when
    it is unavailable.  The field order and NUL framing are part of the
    reproducibility contract.

``claim-fingerprint-v1``
    ``SHA-256(UTF-8("claim-fingerprint-v1\\0" + claim_text))``, where
    ``claim_text`` is casefolded after Unicode whitespace has been collapsed
    to single spaces.  This is a lexical grouping key, not an exact evidence
    identity and not proof of semantic equivalence.

Both helpers return the algorithm/version prefix followed by a colon and the
full, 64-character hexadecimal SHA-256 digest.  No filesystem, clock, network,
or process-specific state is consulted.
"""

from __future__ import annotations

import hashlib

ITEM_FINGERPRINT_ALGORITHM = "item-fingerprint-v1"
CLAIM_FINGERPRINT_ALGORITHM = "claim-fingerprint-v1"


def _text(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value


def _digest(algorithm: str, payload: str) -> str:
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{algorithm}:{digest}"


def legacy_occurrence_id(video_id: str, section: str, source_index: int) -> str:
    """Return the published positional V1 occurrence ID unchanged."""

    _text(video_id, "video_id")
    _text(section, "section")
    if isinstance(source_index, bool) or not isinstance(source_index, int):
        raise TypeError("source_index must be an integer")
    if source_index < 0:
        raise ValueError("source_index must be non-negative")
    return f"{video_id}:{section}:{source_index}"


def item_fingerprint(
    video_id: str,
    section: str,
    source_index: int | None,
    primary_text: str,
) -> str:
    """Return the documented full ``item-fingerprint-v1`` value."""

    _text(video_id, "video_id")
    _text(section, "section")
    _text(primary_text, "primary_text")
    if source_index is None:
        index_text = ""
    else:
        if isinstance(source_index, bool) or not isinstance(source_index, int):
            raise TypeError("source_index must be an integer or None")
        if source_index < 0:
            raise ValueError("source_index must be non-negative")
        index_text = str(source_index)
    payload = "\x00".join(
        (ITEM_FINGERPRINT_ALGORITHM, video_id, section, index_text, primary_text)
    )
    return _digest(ITEM_FINGERPRINT_ALGORITHM, payload)


def collapse_whitespace(value: str) -> str:
    """Collapse all Unicode whitespace runs to one ASCII space."""

    _text(value, "value")
    return " ".join(value.split())


def claim_fingerprint(claim: str) -> str:
    """Return the lexical, case-insensitive ``claim-fingerprint-v1`` value."""

    normalized = collapse_whitespace(_text(claim, "claim")).casefold()
    payload = f"{CLAIM_FINGERPRINT_ALGORITHM}\x00{normalized}"
    return _digest(CLAIM_FINGERPRINT_ALGORITHM, payload)


def evidence_content_id(video_id: str, text: str) -> str:
    """Return an exact-text evidence content ID.

    The digest is over only the raw decoded evidence text encoded as UTF-8.
    The video ID is an outer namespace component, not input to that digest.
    Consequently, case, punctuation, whitespace, and Unicode normalization
    variants produce different content IDs.
    """

    _text(video_id, "video_id")
    _text(text, "text")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"q:{video_id}:{digest}"


# Short aliases keep the helpers convenient at adapter call sites while the
# descriptive names above remain the public documentation surface.
item_id = legacy_occurrence_id
legacy_item_id = legacy_occurrence_id
legacy_id = legacy_occurrence_id
item_fingerprint_v1 = item_fingerprint
claim_fingerprint_v1 = claim_fingerprint
evidence_id = evidence_content_id
content_id = evidence_content_id


__all__ = [
    "CLAIM_FINGERPRINT_ALGORITHM",
    "ITEM_FINGERPRINT_ALGORITHM",
    "claim_fingerprint",
    "claim_fingerprint_v1",
    "collapse_whitespace",
    "content_id",
    "evidence_content_id",
    "evidence_id",
    "item_fingerprint",
    "item_fingerprint_v1",
    "item_id",
    "legacy_id",
    "legacy_item_id",
    "legacy_occurrence_id",
]
