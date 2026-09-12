"""Build and rank the embedded, file://-safe search index."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC
from typing import Any

from .corpus.compiler import OverlayState
from .corpus.normalized_models import NormalizedConcept, NormalizedCorpus, NormalizedVideo
from .corpus.registries import ResolvedLabel
from .slug import concept_slug, video_slug

KIND_ORDER = {
    "video": 0,
    "concept": 1,
    "insight": 2,
    "article": 3,
    "project": 4,
    "deep-dive": 5,
    "question": 6,
    "claim": 7,
}
SECTION_KINDS = {
    "article_ideas": "article",
    "project_ideas": "project",
    "deep_dives": "deep-dive",
    "open_questions": "question",
    "key_claims": "claim",
}


def _truncate(text: str) -> str:
    return text[:2000]


def _joined(*values: Any) -> str:
    return " ".join(str(value) for value in values if value not in (None, ""))


def _relative_root_url(url: str) -> str:
    return f"../{url}"


def _record_identifier(record: dict[str, Any]) -> str:
    return str(
        record.get("record_id")
        or record.get("occurrence_id")
        or record.get("concept_id")
        or record.get("id")
        or ""
    )


def _sort_key(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        KIND_ORDER[record["kind"]],
        record["title"].casefold(),
        record["title"],
        record.get("video_id", ""),
        _record_identifier(record),
    )


def _legacy_video_record(video: dict[str, Any]) -> dict[str, Any]:
    tags = [tag["name"] for tag in video["tags"]]
    summary = video.get("summary", {})
    document = video.get("document", {})
    return {
        "id": video["video_id"],
        "kind": "video",
        "title": video["title"],
        "text": _truncate(_joined(summary.get("markdown"), document.get("description"))),
        "tags": tags,
        "channel": video["channel"],
        "published_date": video["source"]["published_date"],
        "url": _relative_root_url(video["url"]),
    }


def _legacy_records(
    videos: Iterable[dict[str, Any]],
    concepts: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    identifiers: dict[str, tuple[str, str]] = {}

    def register(record: dict[str, Any]) -> None:
        identifier = record["id"]
        previous = identifiers.get(identifier)
        if previous is not None:
            raise ValueError(
                "search global ID collision: "
                f"{identifier!r} is used by {previous[0]} and {record['kind']}"
            )
        identifiers[identifier] = (record["kind"], record["title"])
        records.append(record)

    for video in videos:
        register(_legacy_video_record(video))
        tags = [tag["name"] for tag in video["tags"]]
        for insight in video["core_insights"]:
            register(
                {
                    "id": insight["id"],
                    "kind": "insight",
                    "title": insight["insight"],
                    "text": _truncate(
                        _joined(
                            insight["insight"],
                            insight["why_it_matters"],
                            insight["generalization"],
                            " ".join(quote["text"] for quote in insight["evidence_quotes"]),
                        )
                    ),
                    "tags": tags,
                    "channel": video["channel"],
                    "published_date": video["source"]["published_date"],
                    "url": _relative_root_url(f"{video['url']}#{insight['id']}"),
                }
            )
        for section, kind in SECTION_KINDS.items():
            for item in video[section]:
                title = (
                    item.get("title")
                    or item.get("name")
                    or item.get("topic")
                    or item.get("question")
                    or item.get("claim")
                )
                text = _truncate(
                    _joined(
                        *(
                            value
                            for key, value in item.items()
                            if key not in {"id", "source_index", "video_id", "video_url"}
                            and isinstance(value, (str, int, float, bool))
                        )
                    )
                )
                register(
                    {
                        "id": item["id"],
                        "kind": kind,
                        "title": title,
                        "text": text,
                        "tags": tags,
                        "channel": video["channel"],
                        "published_date": video["source"]["published_date"],
                        "url": _relative_root_url(f"{video['url']}#{item['id']}"),
                    }
                )
    for concept in concepts:
        register(
            {
                "id": concept["id"],
                "kind": "concept",
                "title": concept["name"],
                "text": concept["name"],
                "tags": [concept["name"]],
                "channel": "",
                "published_date": None,
                "url": _relative_root_url(concept["url"]),
            }
        )
    return sorted(records, key=_sort_key)


def _canonical_labels(
    video: NormalizedVideo,
    state: OverlayState | None,
) -> tuple[ResolvedLabel, ...]:
    if state is not None:
        source = state.by_video_id[video.video_id].concepts
        labels = (
            tuple(label for label in source.labels if "::topics[" not in label.location)
            if video.provenance.version == 2
            else source.labels
        )
        endpoints = tuple(
            endpoint
            for connection in source.connections
            for endpoint in (connection.concept, connection.connects_to)
        )
        return labels + endpoints
    from .corpus.registries import Registries

    source = Registries().resolve_source_record(video)
    labels = (
        tuple(label for label in source.labels if "::topics[" not in label.location)
        if video.provenance.version == 2
        else source.labels
    )
    endpoints = tuple(
        endpoint
        for connection in source.connections
        for endpoint in (connection.concept, connection.connects_to)
    )
    return labels + endpoints


def _canonical_label_text(labels: Iterable[ResolvedLabel]) -> list[str]:
    return sorted(
        {label.name for label in labels},
        key=lambda value: (value.casefold(), value),
    )


def _canonical_concept_ids(labels: Iterable[ResolvedLabel]) -> list[str]:
    return sorted({label.id for label in labels})


def _canonical_label_provenance(labels: Iterable[ResolvedLabel]) -> list[dict[str, Any]]:
    return [
        {
            "raw_label": label.raw_label,
            "origin": label.origin.value,
            "location": label.location,
            "resolution_method": label.match_method,
            "canonical_id": label.resolved_id,
            "unresolved": label.unresolved,
        }
        for label in sorted(
            labels,
            key=lambda value: (
                value.raw_label.casefold(),
                value.raw_label,
                value.origin.value,
                value.location,
            ),
        )
    ]


def _canonical_scalar_values(item: Any, section: str) -> list[str]:
    fields = {
        "core_insights": ("insight", "why_it_matters", "generalization"),
        "article_ideas": ("title", "thesis", "angle", "based_on", "audience"),
        "project_ideas": ("name", "hypothesis", "poc", "measurement", "based_on", "raw_fit"),
        "deep_dives": ("topic", "research_question", "why", "trigger_insight", "priority"),
        "open_questions": ("question", "why_unresolved", "research_direction"),
        "key_claims": (
            "claim",
            "claim_type",
            "verification_question",
        ),
    }
    return [
        str(getattr(item, field))
        for field in fields.get(section, ())
        if getattr(item, field, None) not in (None, "")
    ]


def _canonical_video_url(video: NormalizedVideo) -> str:
    return _relative_root_url(_video_url(video))


def _video_url(video: NormalizedVideo) -> str:
    return f"videos/{video_slug(video.title, video.video_id)}/index.html"


def _canonical_record(
    *,
    occurrence_id: str,
    occurrence_kind: str,
    kind: str,
    title: str,
    text: str,
    video: NormalizedVideo,
    labels: tuple[ResolvedLabel, ...],
    url: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "record_id": occurrence_id,
        "occurrence_id": occurrence_id,
        "occurrence_id_kind": occurrence_kind,
        "kind": kind,
        "title": title,
        "text": _truncate(text),
        "concept_ids": _canonical_concept_ids(labels),
        "labels": _canonical_label_provenance(labels),
        "channel": video.channel,
        "published_date": (
            video.source.published_at.astimezone(UTC).date().isoformat()
        ),
        "url": url,
    }
    if extra:
        result.update(extra)
    return result


def _canonical_records(
    corpus: NormalizedCorpus,
    concepts: Iterable[NormalizedConcept] | None = None,
) -> list[dict[str, Any]]:
    state = corpus.overlay_state if isinstance(corpus.overlay_state, OverlayState) else None
    records: list[dict[str, Any]] = []
    occurrence_ids: dict[str, tuple[str, str]] = {}
    concept_nodes: dict[str, dict[str, Any]] = {}

    def register_occurrence(record: dict[str, Any]) -> None:
        occurrence_id = record["occurrence_id"]
        prior = occurrence_ids.get(occurrence_id)
        if prior is not None:
            raise ValueError(
                "search occurrence ID collision: "
                f"{occurrence_id!r} is used by {prior[0]} and {record['kind']}"
            )
        occurrence_ids[occurrence_id] = (record["kind"], record["title"])
        records.append(record)

    def add_concept_label(label: ResolvedLabel) -> None:
        node = concept_nodes.get(label.id)
        provenance = _canonical_label_provenance((label,))
        if node is None:
            concept_nodes[label.id] = {
                "concept_id": label.id,
                "concept_ids": [label.id],
                "kind": "concept",
                "title": label.name,
                "text": label.name,
                "label_names": [label.name],
                "labels": provenance,
                "canonical_id": label.resolved_id,
                "canonical_name": label.canonical_name,
                "unresolved": label.unresolved,
                "url": _relative_root_url(label.url),
                "channel": "",
                "published_date": None,
            }
            return
        names = {*node["label_names"], label.name}
        node["label_names"] = sorted(names, key=lambda value: (value.casefold(), value))
        node["title"] = min(
            node["label_names"],
            key=lambda value: (value.casefold(), value),
        )
        node["text"] = node["title"]
        node["labels"] = sorted(
            [*node["labels"], *provenance],
            key=lambda value: (
                value["raw_label"].casefold(),
                value["raw_label"],
                value["origin"],
                value["location"],
            ),
        )
        node["url"] = min(node["url"], _relative_root_url(label.url))

    for video in sorted(corpus.videos, key=lambda item: item.video_id):
        labels = _canonical_labels(video, state)
        label_names = _canonical_label_text(labels)
        register_occurrence(
            {
                "record_id": video.video_id,
                "occurrence_id": video.video_id,
                "occurrence_id_kind": video.identity.kind.value,
                "kind": "video",
                "title": video.title,
                "text": _truncate(_joined(video.summary.markdown, video.document.description)),
                "concept_ids": _canonical_concept_ids(labels),
                "labels": _canonical_label_provenance(labels),
                "label_names": label_names,
                "channel": video.channel,
                "published_date": (
                    video.source.published_at.astimezone(UTC).date().isoformat()
                ),
                "url": _canonical_video_url(video),
            }
        )
        for label in labels:
            add_concept_label(label)
        for section, kind in SECTION_KINDS.items():
            for item in sorted(getattr(video, section), key=lambda value: (
                value.id_kind.value != "legacy-position",
                value.source_index if value.source_index is not None else 10**12,
                value.id,
            )):
                title = (
                    getattr(item, "title", None)
                    or getattr(item, "name", None)
                    or getattr(item, "topic", None)
                    or getattr(item, "question", None)
                    or getattr(item, "claim", None)
                )
                text_values = _canonical_scalar_values(item, section)
                evidence = getattr(item, "evidence", ())
                evidence_values = (
                    evidence
                    if isinstance(evidence, tuple)
                    else (evidence,)
                    if hasattr(evidence, "text")
                    else ()
                )
                text_values.extend(value.text for value in evidence_values)
                register_occurrence(
                    _canonical_record(
                        occurrence_id=item.id,
                        occurrence_kind=item.id_kind.value,
                        kind=kind,
                        title=title,
                        text=_joined(*text_values),
                        video=video,
                        labels=labels,
                        url=_relative_root_url(f"{_video_url(video)}#{item.id}"),
                        extra={
                            "fingerprint": item.fingerprint.value,
                            "source_index": item.source_index,
                            **(
                                {
                                    "verification_requested": item.verification_requested,
                                    "review_status": (
                                        next(
                                            (
                                                resolution.status
                                                for resolution in state.by_video_id[
                                                    video.video_id
                                                ].claims
                                                if resolution.occurrence_id == item.id
                                            ),
                                            None,
                                        )
                                        if state is not None
                                        else None
                                    ),
                                }
                                if section == "key_claims"
                                else {}
                            ),
                        },
                    )
                )

    explicit_concepts = tuple(concepts if concepts is not None else corpus.concepts)
    for concept in explicit_concepts:
        # E1's NormalizedConcept has no registry ID.  It is accepted here as
        # a source of canonical names only; overlay-resolved occurrences own
        # the stable IDs.
        concept_id = getattr(concept, "id", None) or f"concept:{concept.name}"
        node = concept_nodes.setdefault(
            concept_id,
            {
                "concept_id": concept_id,
                "concept_ids": [concept_id],
                "kind": "concept",
                "title": concept.name,
                "text": concept.name,
                "label_names": [concept.name],
                "labels": [],
                "canonical_id": concept_id,
                "canonical_name": concept.name,
                "unresolved": False,
                "url": _relative_root_url(
                    f"concepts/{concept_slug(concept.name)}/index.html"
                ),
                "channel": "",
                "published_date": None,
            },
        )
        if node["title"] != concept.name:
            node["title"] = min(
                (node["title"], concept.name),
                key=lambda value: (value.casefold(), value),
            )
            node["text"] = node["title"]
    if state is not None:
        for entry in state.registries.concepts:
            concept_nodes.setdefault(
                entry.id,
                {
                    "concept_id": entry.id,
                    "concept_ids": [entry.id],
                    "kind": "concept",
                    "title": entry.canonical_name,
                    "text": entry.canonical_name,
                    "label_names": [entry.canonical_name],
                    "labels": [],
                    "canonical_id": entry.id,
                    "canonical_name": entry.canonical_name,
                    "unresolved": False,
                    "url": _relative_root_url(f"concepts/{entry.id}/index.html"),
                    "channel": "",
                    "published_date": None,
                },
            )

    all_ids = set(occurrence_ids)
    for concept_id in concept_nodes:
        if concept_id in all_ids:
            raise ValueError(
                "search global ID collision: "
                f"{concept_id!r} is both an occurrence ID and concept ID"
            )
        all_ids.add(concept_id)
    records.extend(concept_nodes.values())
    return sorted(records, key=_sort_key)


def build_search_records(
    videos: Iterable[Any] | NormalizedCorpus,
    concepts: Iterable[Any] | None = None,
) -> list[dict[str, Any]]:
    """Build records from canonical normalized data or the V1 projection.

    Canonical records expose ``occurrence_id`` and ``concept_ids``.  Legacy
    mapping inputs retain the old ``id`` and display-name ``tags`` fields so
    existing site bytes remain unchanged until E3-T4.
    """

    if isinstance(videos, NormalizedCorpus):
        if concepts is not None:
            raise TypeError("concepts must be omitted when passing a NormalizedCorpus")
        return _canonical_records(videos)
    values = tuple(videos)
    if values and all(isinstance(value, NormalizedVideo) for value in values):
        corpus = NormalizedCorpus(
            videos=values,
            concepts=tuple(concepts or ()),
            index_items=(),
            warnings=(),
        )
        return _canonical_records(corpus, tuple(concepts or ()))
    return _legacy_records(values, tuple(concepts or ()))


def _tokens(value: str) -> list[str]:
    return [token for token in re.split(r"\W+", value.casefold(), flags=re.UNICODE) if token]


def _record_haystack(record: dict[str, Any]) -> str:
    labels = record.get("label_names")
    if labels is None:
        labels = record.get("tags", ())
    return _joined(
        record["title"],
        record["text"],
        " ".join(
            label if isinstance(label, str) else label.get("raw_label", "")
            for label in labels
        ),
        record.get("channel"),
    ).casefold()


def rank_search_records(query: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    query_tokens = _tokens(query)
    if not query_tokens:
        return []
    query_casefolded = query.casefold().strip()
    matches = []
    for record in records:
        title = record["title"]
        haystack = _record_haystack(record)
        if not all(token in haystack for token in query_tokens):
            continue
        title_tokens = _tokens(title)
        occurrences = sum(haystack.count(token) for token in query_tokens)
        matches.append(
            (
                title.casefold() == query_casefolded,
                title.casefold().startswith(query_casefolded),
                all(token in title_tokens for token in query_tokens),
                occurrences,
                record,
            )
        )
    matches.sort(
        key=lambda match: (
            not match[0],
            not match[1],
            not match[2],
            -match[3],
            KIND_ORDER[match[4]["kind"]],
            match[4]["title"].casefold(),
            match[4]["title"],
            _record_identifier(match[4]),
        )
    )
    return [match[-1] for match in matches]
