"""Deterministic concept adjacency graph calculations."""

from __future__ import annotations

import math
from typing import Any

from .slug import edge_id

WIDTH = 1200
HEIGHT = 800
MAX_DISPLAY_NODES = 60


def _place_nodes(nodes: list[dict[str, Any]]) -> None:
    ranked = sorted(
        nodes,
        key=lambda node: (-node["degree"], -node["tag_video_count"], node["name"], node["id"]),
    )
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
    for node in nodes:
        if node in shown:
            continue
        else:
            node["x"], node["y"] = 0, 0


def build_concept_graph(
    concepts: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    videos: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    """Aggregate directed connection records into an undirected display graph."""

    nodes = [dict(node) for node in concepts]
    by_id = {node["id"]: node for node in nodes}
    aggregate: dict[tuple[str, str], dict[str, Any]] = {}
    for video in videos:
        for connection in video["connections"]:
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
            by_id[edge["source"]]["degree"] += 1
            by_id[edge["target"]]["degree"] += 1
        edges.append(
            {
                **edge,
                "relationships": sorted(edge["relationships"], key=lambda value: value.casefold()),
                "video_ids": sorted(edge["video_ids"]),
            }
        )
    _place_nodes(nodes)
    display_nodes = sorted(
        nodes,
        key=lambda node: (-node["degree"], -node["tag_video_count"], node["name"], node["id"]),
    )[:MAX_DISPLAY_NODES]
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
