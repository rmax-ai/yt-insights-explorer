"""Pure compilation entry points for version-neutral corpus records.

The E1 compiler boundary accepts already parsed, immutable source records or
already adapted normalized videos.  It performs only deterministic
in-memory transformations:

* V1 records are adapted with :func:`corpus.adapters.v1.adapt_v1`.
* normalized videos are retained unchanged.
* the returned :class:`NormalizedCorpus` has no registry or ledger state.

This module never loads a registry or ledger, opens a file, writes a file,
uses the network, or reads the clock.  Callers own parsing, I/O, overlays,
and rendering.  The optional overlay and version-specific orchestration
layers belong to later migration tasks.
"""

from __future__ import annotations

from collections.abc import Iterable

from .adapters.v1 import adapt_v1
from .normalized_models import (
    NormalizedCorpus,
    NormalizedIndexItem,
    NormalizedVideo,
    Provenance,
    SourceVersion,
)
from .source_models import RawVideo, V1SourceRecord


def compile_source_records(
    records: Iterable[V1SourceRecord | RawVideo],
) -> tuple[NormalizedVideo, ...]:
    """Adapt parsed V1 records in input order without performing I/O."""

    return tuple(adapt_v1(record) for record in records)


def _compile_v1_corpus(records: tuple[V1SourceRecord | RawVideo, ...]) -> NormalizedCorpus:
    videos = compile_source_records(records)
    index_items = tuple(
        NormalizedIndexItem(
            video_id=record.index.video_id,
            title=record.index.title,
            channel=record.index.channel,
            status=record.index.status,
            ingested_at=record.index.ingested_at,
            cost_usd_total=record.index.cost_usd_total,
            provenance=Provenance(
                source_version=SourceVersion.V1,
                location=f"index.json::items[{index}]",
                source_index=index,
            ),
        )
        for index, record in enumerate(records)
    )
    return NormalizedCorpus(
        videos=videos,
        concepts=(),
        index_items=index_items,
        warnings=(),
    )


def compile_corpus(
    records: Iterable[V1SourceRecord | RawVideo | NormalizedVideo]
    | V1SourceRecord
    | RawVideo
    | NormalizedVideo,
    overlays: object | None = None,
) -> NormalizedCorpus:
    """Compile parsed V1 records or normalized videos into an in-memory corpus.

    The iterable is consumed once and never retained.  Supplying source
    records creates normalized index metadata; supplying normalized videos is
    useful for later compiler stages and intentionally leaves index metadata
    empty because that information is not present on a normalized video.
    """

    del overlays  # Overlay application is intentionally outside the E1 boundary.
    if isinstance(records, (V1SourceRecord, RawVideo, NormalizedVideo)):
        values = (records,)
    else:
        values = tuple(records)
    if all(
        isinstance(value, RawVideo) and value.source_version.value == int(SourceVersion.V1)
        for value in values
    ):
        return _compile_v1_corpus(values)  # type: ignore[arg-type]
    if all(isinstance(value, NormalizedVideo) for value in values):
        return NormalizedCorpus(
            videos=values,  # type: ignore[arg-type]
            concepts=(),
            index_items=(),
            warnings=(),
        )
    raise TypeError("records must contain only V1SourceRecord or only NormalizedVideo values")


def compile_v1(records: Iterable[V1SourceRecord]) -> NormalizedCorpus:
    """Compile parsed V1 records through the pure E1 adapter boundary."""

    return compile_corpus(records)


compile = compile_corpus
compile_records = compile_corpus


__all__ = [
    "compile",
    "compile_corpus",
    "compile_records",
    "compile_source_records",
    "compile_v1",
]
