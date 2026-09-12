"""Deterministic compilation and internal overlay application.

Source records are adapted first.  Overlays are then applied in the fixed
order ``concepts/topics -> projects -> claim reviews``.  The resulting
resolution state is retained only on the in-memory compiler result;
``normalize_corpus`` continues to project the pre-overlay dictionary shape.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .adapters.v1 import adapt_v1
from .ledgers import ClaimLedger, ClaimResolution
from .normalized_models import (
    LabelOrigin,
    NormalizedCorpus,
    NormalizedIndexItem,
    NormalizedVideo,
    Provenance,
    RawLabel,
    SourceVersion,
)
from .registries import (
    ProjectResolution,
    Registries,
    ResolvedLabel,
    ResolvedSource,
    load_registries,
    resolve_project,
    resolve_topic,
)
from .source_models import RawVideo, V1SourceRecord

OVERLAY_APPLICATION_ORDER = ("concepts/topics", "projects", "claim reviews")
OVERLAY_ORDER = OVERLAY_APPLICATION_ORDER


@dataclass(frozen=True, slots=True)
class ResolvedVideoState:
    """Internal overlay results for one adapted video."""

    video_id: str
    concepts: ResolvedSource
    topics: tuple[ResolvedLabel, ...]
    projects: tuple[ProjectResolution, ...]
    claims: tuple[ClaimResolution, ...]

    @property
    def concept_resolution(self) -> ResolvedSource:
        return self.concepts

    @property
    def resolved_concepts(self) -> tuple[ResolvedLabel, ...]:
        return self.concepts.concept_occurrences

    @property
    def resolved_topics(self) -> tuple[ResolvedLabel, ...]:
        return self.topics

    @property
    def resolved_projects(self) -> tuple[ProjectResolution, ...]:
        return self.projects

    @property
    def claim_reviews(self) -> tuple[ClaimResolution, ...]:
        return self.claims

    @property
    def resolved_claims(self) -> tuple[ClaimResolution, ...]:
        return self.claims


@dataclass(frozen=True, slots=True)
class OverlayState:
    """Compiler-only state produced by all overlay stages."""

    registries: Registries
    ledger: ClaimLedger
    videos: tuple[ResolvedVideoState, ...]
    application_order: tuple[str, ...] = OVERLAY_APPLICATION_ORDER

    def __post_init__(self) -> None:
        if self.application_order != OVERLAY_APPLICATION_ORDER:
            raise ValueError(
                "overlay application order must be "
                + " -> ".join(OVERLAY_APPLICATION_ORDER)
            )

    @property
    def by_video_id(self) -> dict[str, ResolvedVideoState]:
        return {video.video_id: video for video in self.videos}

    @property
    def resolved_videos(self) -> tuple[ResolvedVideoState, ...]:
        return self.videos

    @property
    def resolved(self) -> OverlayState:
        return self

    @property
    def claims(self) -> tuple[ClaimResolution, ...]:
        return tuple(claim for video in self.videos for claim in video.claims)

    @property
    def concepts(self) -> tuple[ResolvedSource, ...]:
        return tuple(video.concepts for video in self.videos)

    @property
    def concept_resolutions(self) -> tuple[ResolvedSource, ...]:
        return self.concepts

    @property
    def topics(self) -> tuple[tuple[ResolvedLabel, ...], ...]:
        return tuple(video.topics for video in self.videos)

    @property
    def topic_resolutions(self) -> tuple[tuple[ResolvedLabel, ...], ...]:
        return self.topics

    @property
    def projects(self) -> tuple[tuple[ProjectResolution, ...], ...]:
        return tuple(video.projects for video in self.videos)

    @property
    def project_resolutions(self) -> tuple[tuple[ProjectResolution, ...], ...]:
        return self.projects

    @property
    def claim_resolutions(self) -> tuple[ClaimResolution, ...]:
        return self.claims

    @property
    def claim_reviews(self) -> tuple[ClaimResolution, ...]:
        return self.claims


CorpusOverlays = Registries
ResolvedCorpusState = OverlayState


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


def _coerce_registries(overlays: object | None) -> Registries:
    if overlays is None:
        return Registries()
    if isinstance(overlays, Registries):
        return overlays
    if isinstance(overlays, (str, Path)):
        return load_registries(overlay_root=overlays)
    if isinstance(overlays, Mapping):
        return load_registries(documents=overlays)
    raise TypeError(
        "overlays must be a Registries value, source/overlay root, "
        "overlay document mapping, or None"
    )


def _source_label(
    label: str,
    *,
    origin: LabelOrigin,
    location: str,
) -> RawLabel:
    return RawLabel(label=label, origin=origin, location=location)


def _topic_resolutions(registries: Registries, video: NormalizedVideo) -> tuple[ResolvedLabel, ...]:
    return tuple(
        resolve_topic(
            registries,
            _source_label(
                item.topic,
                origin=LabelOrigin.SOURCE_ARTIFACT,
                location=f"{item.provenance.location}.topic",
            ),
        )
        for item in video.deep_dives
    )


def _project_resolutions(
    registries: Registries,
    video: NormalizedVideo,
) -> tuple[ProjectResolution, ...]:
    return tuple(
        resolve_project(
            registries,
            item.raw_fit,
            location=f"{item.provenance.location}.fits",
            origin=LabelOrigin.SOURCE_ARTIFACT,
        )
        for item in video.project_ideas
    )


def _apply_overlays(
    corpus: NormalizedCorpus,
    registries: Registries,
) -> NormalizedCorpus:
    """Apply every overlay stage after adaptation, without changing facts."""

    # Stage 1: concepts and topics.  Project fits are deliberately deferred
    # to the next stage so the order is observable and stable.
    concept_resolutions = tuple(
        registries.resolve_source_record(video, include_projects=False)
        for video in corpus.videos
    )
    topic_resolutions = tuple(
        _topic_resolutions(registries, video) for video in corpus.videos
    )

    # Stage 2: projects.
    project_resolutions = tuple(
        _project_resolutions(registries, video) for video in corpus.videos
    )

    # Stage 3: claim reviews.  Reference and fingerprint checks happen only
    # after all adapted claim occurrences are available.
    ledger = ClaimLedger.from_registries(registries)
    claim_resolutions = ledger.apply(corpus)
    claims_by_video: dict[str, list[ClaimResolution]] = {}
    for claim in claim_resolutions:
        claims_by_video.setdefault(claim.occurrence.video_id, []).append(claim)

    resolved_videos = tuple(
        ResolvedVideoState(
            video_id=video.video_id,
            concepts=concept,
            topics=topics,
            projects=projects,
            claims=tuple(claims_by_video.get(video.video_id, ())),
        )
        for video, concept, topics, projects in zip(
            corpus.videos,
            concept_resolutions,
            topic_resolutions,
            project_resolutions,
            strict=True,
        )
    )
    state = OverlayState(
        registries=registries,
        ledger=ledger,
        videos=resolved_videos,
    )
    return NormalizedCorpus(
        videos=corpus.videos,
        concepts=corpus.concepts,
        index_items=corpus.index_items,
        warnings=corpus.warnings,
        overlay_state=state,
    )


def compile_corpus(
    records: Iterable[V1SourceRecord | RawVideo | NormalizedVideo]
    | V1SourceRecord
    | RawVideo
    | NormalizedVideo,
    overlays: object | None = None,
    *,
    overlay_root: str | Path | None = None,
) -> NormalizedCorpus:
    """Compile records and apply optional source-relative overlays.

    ``overlays`` accepts a :class:`Registries` bundle, a source/overlay root,
    or injected overlay documents.  ``None`` means all optional overlays are
    absent.  In every case, only the internal ``overlay_state`` changes;
    videos, index items, concepts, and warnings retain the E1 values.
    """

    if overlays is not None and overlay_root is not None:
        raise TypeError("compile_corpus accepts overlays or overlay_root, not both")
    if overlay_root is not None:
        overlays = overlay_root
    if isinstance(records, (V1SourceRecord, RawVideo, NormalizedVideo)):
        values = (records,)
    else:
        values = tuple(records)
    if all(
        isinstance(value, RawVideo) and value.source_version.value == int(SourceVersion.V1)
        for value in values
    ):
        corpus = _compile_v1_corpus(values)  # type: ignore[arg-type]
    elif all(isinstance(value, NormalizedVideo) for value in values):
        corpus = NormalizedCorpus(
            videos=values,  # type: ignore[arg-type]
            concepts=(),
            index_items=(),
            warnings=(),
        )
    else:
        raise TypeError("records must contain only V1SourceRecord or only NormalizedVideo values")
    return _apply_overlays(corpus, _coerce_registries(overlays))


def apply_overlays(
    corpus: NormalizedCorpus,
    overlays: object | None = None,
    *,
    overlay_root: str | Path | None = None,
) -> NormalizedCorpus:
    """Apply overlays to an already adapted corpus."""

    if overlays is not None and overlay_root is not None:
        raise TypeError("apply_overlays accepts overlays or overlay_root, not both")
    if overlay_root is not None:
        overlays = overlay_root
    return _apply_overlays(corpus, _coerce_registries(overlays))


def compile_v1(
    records: Iterable[V1SourceRecord],
    overlays: object | None = None,
    *,
    overlay_root: str | Path | None = None,
) -> NormalizedCorpus:
    """Compile parsed V1 records through adaptation and overlay stages."""

    return compile_corpus(records, overlays=overlays, overlay_root=overlay_root)


compile = compile_corpus
compile_records = compile_corpus


__all__ = [
    "CorpusOverlays",
    "OVERLAY_APPLICATION_ORDER",
    "OVERLAY_ORDER",
    "OverlayState",
    "ResolvedCorpusState",
    "ResolvedVideoState",
    "apply_overlays",
    "compile",
    "compile_corpus",
    "compile_records",
    "compile_source_records",
    "compile_v1",
]
