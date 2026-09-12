"""Deterministic concept adjacency graph calculations."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from .corpus.compiler import OverlayState
from .corpus.normalized_models import NormalizedConcept, NormalizedCorpus, NormalizedVideo
from .corpus.registries import ResolvedConnection, ResolvedLabel
from .slug import concept_slug, edge_id

WIDTH = 1200
HEIGHT = 800
MAX_DISPLAY_NODES = 60


def _node_sort_key(node: dict[str, Any]) -> tuple[Any, ...]:
    """Sort registry IDs first, then use the old deterministic legacy slug."""

    canonical_id = node.get("canonical_id") or node.get("registry_id")
    if canonical_id:
        return (0, canonical_id, node.get("name", ""))
    return (
        1,
        node.get("slug", concept_slug(node.get("name", ""))),
        node.get("name", "").casefold(),
        node.get("name", ""),
        node["id"],
    )


def _rank_key(node: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -node["degree"],
        -node["tag_video_count"],
        node["name"].casefold(),
        node["name"],
        node["id"],
    )


def _place_nodes(nodes: list[dict[str, Any]]) -> None:
    ranked = sorted(nodes, key=_rank_key)
    shown = ranked[:MAX_DISPLAY_NODES]
    center = shown[:1]
    if center:
        center[0]["x"], center[0]["y"] = WIDTH // 2, HEIGHT // 2
    remaining = shown[1:]
    ring_number = 0
    offset = 0
    capacity = 8
    while offset < len(remaining):
        ring = remaining[offset : offset + capacity]
        radius = min(WIDTH, HEIGHT) * (0.16 + ring_number * 0.12)
        for slot, node in enumerate(ring):
            angle = -math.pi / 2 + 2 * math.pi * slot / capacity
            node["x"] = round(WIDTH / 2 + radius * math.cos(angle))
            node["y"] = round(HEIGHT / 2 + radius * math.sin(angle))
        offset += len(ring)
        ring_number += 1
        capacity += 8
    shown_ids = {node["id"] for node in shown}
    for node in nodes:
        if node["id"] not in shown_ids:
            node["x"], node["y"] = 0, 0


def _resolved_label_dict(label: ResolvedLabel) -> dict[str, Any]:
    return {
        "raw_label": label.raw_label,
        "origin": label.origin.value,
        "location": label.location,
        "resolution_method": label.match_method,
        "canonical_id": label.resolved_id,
        "canonical_name": label.canonical_name,
        "unresolved": label.unresolved,
    }


def _canonical_inputs(
    concepts: Iterable[Any] | NormalizedCorpus,
    videos: Iterable[Any] | None,
) -> tuple[tuple[NormalizedVideo, ...], OverlayState | None]:
    if isinstance(concepts, NormalizedCorpus):
        corpus = concepts
        if videos is not None:
            raise TypeError("videos must be omitted when passing a NormalizedCorpus")
        values = corpus.videos
        state = corpus.overlay_state
    else:
        values = tuple(videos or ())
        state = None
    if not all(isinstance(video, NormalizedVideo) for video in values):
        raise TypeError("canonical graph inputs must contain NormalizedVideo values")
    return tuple(values), state if isinstance(state, OverlayState) else None


def _resolved_source(
    video: NormalizedVideo,
    state: OverlayState | None,
) -> tuple[tuple[ResolvedLabel, ...], tuple[ResolvedConnection, ...]]:
    if state is not None:
        source = state.by_video_id[video.video_id].concepts
        labels = (
            tuple(label for label in source.labels if "::topics[" not in label.location)
            if video.provenance.version == 2
            else source.labels
        )
        return labels, source.connections
    # With no overlay, use the same resolver against an empty registry.  This
    # preserves raw labels and produces deterministic unresolved nodes.
    from .corpus.registries import Registries

    source = Registries().resolve_source_record(video)
    labels = (
        tuple(label for label in source.labels if "::topics[" not in label.location)
        if video.provenance.version == 2
        else source.labels
    )
    return labels, source.connections


def _build_canonical_graph(
    concepts: Iterable[NormalizedConcept],
    videos: tuple[NormalizedVideo, ...],
    state: OverlayState | None,
) -> dict[str, Any]:
    """Build graph nodes from resolved occurrences, never display names."""

    node_occurrences: dict[str, list[ResolvedLabel]] = defaultdict(list)
    tag_video_ids: dict[str, set[str]] = defaultdict(set)
    connection_video_ids: dict[str, set[str]] = defaultdict(set)
    connection_records: list[tuple[str, ResolvedConnection, str]] = []
    for video in sorted(videos, key=lambda item: item.video_id):
        labels, connections = _resolved_source(video, state)
        for label in labels:
            node_occurrences[label.id].append(label)
            tag_video_ids[label.id].add(video.video_id)
        for connection in connections:
            connection_records.append((video.video_id, connection, video.video_id))
            for endpoint in (connection.concept, connection.connects_to):
                node_occurrences[endpoint.id].append(endpoint)
                connection_video_ids[endpoint.id].add(video.video_id)

    # ``concepts`` is deliberately included as a source of canonical registry
    # entries when it is populated by a future compiler release.  E1/E2 keep
    # normalized concepts empty, so the resolved occurrence records above are
    # the authoritative input today.
    del concepts
    if state is not None:
        for entry in state.registries.concepts:
            node_occurrences.setdefault(
                entry.id,
                [
                    ResolvedLabel(
                        raw_label=entry.canonical_name,
                        origin="source_artifact",
                        location=entry.location,
                        resolved_id=entry.id,
                        canonical_name=entry.canonical_name,
                        match_method="canonical_name",
                        url=f"concepts/{entry.id}/index.html",
                        registry_kind="concept",
                        status=entry.status,
                    )
                ],
            )
    nodes: list[dict[str, Any]] = []
    for node_id in sorted(node_occurrences):
        occurrences = sorted(
            node_occurrences[node_id],
            key=lambda label: (
                label.name.casefold(),
                label.name,
                label.raw_label.casefold(),
                label.raw_label,
                label.origin.value,
                label.location,
            ),
        )
        first = occurrences[0]
        name = first.name
        nodes.append(
            {
                "id": node_id,
                "name": name,
                "slug": first.resolved_id or concept_slug(name),
                "url": first.url,
                "video_ids": sorted(
                    tag_video_ids[node_id] | connection_video_ids[node_id]
                ),
                "tag_video_count": len(tag_video_ids[node_id]),
                "connection_video_count": len(connection_video_ids[node_id]),
                "degree": 0,
                "x": 0,
                "y": 0,
                "canonical_id": first.resolved_id,
                "canonical_name": first.canonical_name,
                "unresolved": first.unresolved,
                "resolutions": [
                    _resolved_label_dict(label)
                    for label in sorted(
                        set(occurrences),
                        key=lambda label: (
                            label.raw_label.casefold(),
                            label.raw_label,
                            label.origin.value,
                            label.location,
                        ),
                    )
                ],
            }
        )

    by_id = {node["id"]: node for node in nodes}
    aggregate: dict[tuple[str, str], dict[str, Any]] = {}
    for video_id, connection, _ in sorted(
        connection_records,
        key=lambda item: (
            item[0],
            min(item[1].concept.id, item[1].connects_to.id),
            max(item[1].concept.id, item[1].connects_to.id),
            item[1].relationship.casefold(),
            item[1].relationship,
            item[1].location,
        ),
    ):
        source = connection.concept.id
        target = connection.connects_to.id
        low, high = sorted((source, target))
        key = (low, high)
        edge = aggregate.setdefault(
            key,
            {
                "id": edge_id(low, high),
                "source": low,
                "target": high,
                "relationships": set(),
                "video_ids": set(),
                "occurrence_count": 0,
                "occurrences": [],
            },
        )
        edge["relationships"].add(connection.relationship)
        edge["video_ids"].add(video_id)
        edge["occurrence_count"] += 1
        edge["occurrences"].append(
            {
                "video_id": video_id,
                "source_occurrence_id": connection.concept.id,
                "target_occurrence_id": connection.connects_to.id,
                "location": connection.location,
            }
        )

    edges = []
    for edge in sorted(aggregate.values(), key=lambda item: item["id"]):
        if edge["source"] != edge["target"]:
            by_id[edge["source"]]["degree"] += 1
            by_id[edge["target"]]["degree"] += 1
        edges.append(
            {
                **edge,
                "relationships": sorted(
                    edge["relationships"],
                    key=lambda value: (value.casefold(), value),
                ),
                "video_ids": sorted(edge["video_ids"]),
                "occurrences": sorted(
                    edge["occurrences"],
                    key=lambda item: (
                        item["video_id"],
                        item["source_occurrence_id"],
                        item["target_occurrence_id"],
                        item["location"],
                    ),
                ),
            }
        )
        edges[-1].pop("_unused", None)

    _place_nodes(nodes)
    display_nodes = sorted(nodes, key=_rank_key)[:MAX_DISPLAY_NODES]
    display_ids = {node["id"] for node in display_nodes}
    display_edge_ids = [
        edge["id"]
        for edge in edges
        if edge["source"] != edge["target"]
        and edge["source"] in display_ids
        and edge["target"] in display_ids
    ]
    return {
        "schema_version": 1,
        "nodes": sorted(nodes, key=_node_sort_key),
        "edges": edges,
        "graph": {
            "width": WIDTH,
            "height": HEIGHT,
            "display_node_ids": [node["id"] for node in display_nodes],
            "display_edge_ids": display_edge_ids,
        },
    }


def _legacy_graph_projection(graph: dict[str, Any]) -> dict[str, Any]:
    """Drop canonical provenance while preserving the V1 graph shape."""

    nodes = [
        {
            key: node[key]
            for key in (
                "id",
                "name",
                "slug",
                "url",
                "video_ids",
                "tag_video_count",
                "connection_video_count",
                "degree",
                "x",
                "y",
            )
        }
        for node in graph["nodes"]
    ]
    edges = [
        {
            key: edge[key]
            for key in (
                "id",
                "source",
                "target",
                "relationships",
                "video_ids",
                "occurrence_count",
            )
        }
        for edge in graph["edges"]
    ]
    return {
        "schema_version": graph["schema_version"],
        "nodes": nodes,
        "edges": edges,
        "graph": graph["graph"],
    }


def _build_legacy_graph(
    concepts: Iterable[dict[str, Any]],
    videos: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Compatibility projection for the unchanged V1 renderer contract."""

    nodes = []
    for source in concepts:
        node = dict(source)
        node.setdefault("degree", 0)
        node.setdefault("tag_video_count", 0)
        node.setdefault("connection_video_count", 0)
        node.setdefault("x", 0)
        node.setdefault("y", 0)
        nodes.append(node)
    nodes.sort(key=_node_sort_key)
    by_id = {node["id"]: node for node in nodes}
    aggregate: dict[tuple[str, str], dict[str, Any]] = {}
    for video in sorted(videos, key=lambda item: item["video_id"]):
        for connection in sorted(
            video["connections"],
            key=lambda item: (
                min(item["concept_id"], item["connects_to_id"]),
                max(item["concept_id"], item["connects_to_id"]),
                item["relationship"].casefold(),
                item["relationship"],
            ),
        ):
            source = connection["concept_id"]
            target = connection["connects_to_id"]
            low, high = sorted((source, target))
            key = (low, high)
            edge = aggregate.setdefault(
                key,
                {
                    "id": edge_id(low, high),
                    "source": low,
                    "target": high,
                    "relationships": set(),
                    "video_ids": set(),
                    "occurrence_count": 0,
                },
            )
            edge["relationships"].add(connection["relationship"])
            edge["video_ids"].add(video["video_id"])
            edge["occurrence_count"] += 1
    edges = []
    for edge in sorted(aggregate.values(), key=lambda item: item["id"]):
        if edge["source"] != edge["target"]:
            if edge["source"] in by_id:
                by_id[edge["source"]]["degree"] += 1
            if edge["target"] in by_id:
                by_id[edge["target"]]["degree"] += 1
        edges.append(
            {
                **edge,
                "relationships": sorted(edge["relationships"], key=lambda value: value.casefold()),
                "video_ids": sorted(edge["video_ids"]),
            }
        )
    _place_nodes(nodes)
    display_nodes = sorted(nodes, key=_rank_key)[:MAX_DISPLAY_NODES]
    display_ids = {node["id"] for node in display_nodes}
    display_edge_ids = [
        edge["id"]
        for edge in edges
        if edge["source"] != edge["target"]
        and edge["source"] in display_ids
        and edge["target"] in display_ids
    ]
    return {
        "schema_version": 1,
        "nodes": nodes,
        "edges": edges,
        "graph": {
            "width": WIDTH,
            "height": HEIGHT,
            "display_node_ids": [node["id"] for node in display_nodes],
            "display_edge_ids": display_edge_ids,
        },
    }


def build_concept_graph(
    concepts: Iterable[Any] | NormalizedCorpus,
    videos: Iterable[Any] | None = None,
    *,
    compatibility: bool | None = None,
) -> dict[str, Any]:
    """Build an undirected graph from canonical concepts and connections.

    A ``NormalizedCorpus`` is the preferred call form.  The two-argument
    canonical form is also accepted for callers that already hold its pieces.
    Legacy dictionaries remain a compatibility-only input and are sorted
    canonically rather than preserving caller order.
    """

    if isinstance(concepts, NormalizedCorpus):
        canonical_videos, state = _canonical_inputs(concepts, None)
        if compatibility:
            return _legacy_graph_projection(
                _build_canonical_graph(concepts.concepts, canonical_videos, None)
            )
        return _build_canonical_graph(concepts.concepts, canonical_videos, state)

    values = tuple(concepts)
    video_values = tuple(videos or ())
    canonical_concepts = bool(values) and all(
        isinstance(value, NormalizedConcept) for value in values
    )
    canonical_videos = bool(video_values) and all(
        isinstance(value, NormalizedVideo) for value in video_values
    )
    if canonical_concepts or (not values and canonical_videos):
        canonical_video_values, state = _canonical_inputs(values, video_values)
        if compatibility:
            return _legacy_graph_projection(
                _build_canonical_graph(values, canonical_video_values, None)
            )
        return _build_canonical_graph(values, canonical_video_values, state)
    return _build_legacy_graph(values, video_values)
