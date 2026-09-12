from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from yt_insights_web.corpus.compiler import compile_corpus
from yt_insights_web.graph import build_concept_graph
from yt_insights_web.load import load_corpus
from yt_insights_web.serialize import json_text

ROOT = Path(__file__).resolve().parents[1]
CONSUMER_FIXTURE = ROOT / "tests" / "fixtures" / "consumer-cutover"


def concepts(names: list[str]) -> list[dict]:
    return [
        {
            "id": f"c-{index}",
            "name": name,
            "slug": name,
            "url": f"concepts/{name}/index.html",
            "video_ids": [],
            "tag_video_count": index + 1,
            "connection_video_count": 0,
            "degree": 0,
            "x": 0,
            "y": 0,
        }
        for index, name in enumerate(names)
    ]


def test_graph_aggregates_directional_duplicates_and_omits_self_edges_from_display() -> None:
    nodes = concepts(["alpha", "beta"])
    videos = [
        {
            "video_id": "v1",
            "connections": [
                {
                    "concept_id": "c-0",
                    "connects_to_id": "c-1",
                    "relationship": "supports",
                },
                {
                    "concept_id": "c-1",
                    "connects_to_id": "c-0",
                    "relationship": "reverse",
                },
                {
                    "concept_id": "c-0",
                    "connects_to_id": "c-0",
                    "relationship": "self",
                },
            ],
        }
    ]

    graph = build_concept_graph(nodes, videos)
    edge = next(edge for edge in graph["edges"] if edge["source"] != edge["target"])

    assert edge["occurrence_count"] == 2
    assert edge["video_ids"] == ["v1"]
    assert set(edge["relationships"]) == {"reverse", "supports"}
    assert len(graph["edges"]) == 2
    assert all(
        edge_id not in graph["graph"]["display_edge_ids"]
        for edge_id in [item["id"] for item in graph["edges"] if item["source"] == item["target"]]
    )


def test_graph_layout_is_repeatable_and_capped_at_sixty_nodes() -> None:
    nodes = concepts([f"node-{index:02d}" for index in range(61)])
    graph_a = build_concept_graph(nodes, [])
    graph_b = build_concept_graph(nodes, [])

    positions_a = [(node["id"], node["x"], node["y"]) for node in graph_a["nodes"]]
    positions_b = [(node["id"], node["x"], node["y"]) for node in graph_b["nodes"]]

    assert positions_a == positions_b
    assert len(graph_a["graph"]["display_node_ids"]) == 60
    assert graph_a["graph"]["width"] == 1200
    assert graph_a["graph"]["height"] == 800


def test_canonical_graph_resolves_registry_nodes_and_keeps_endpoint_only_nodes() -> None:
    loaded = load_corpus(CONSUMER_FIXTURE)
    corpus = compile_corpus(loaded.videos, overlay_root=CONSUMER_FIXTURE)

    graph = build_concept_graph(corpus)
    by_id = {node["id"]: node for node in graph["nodes"]}

    canonical = by_id["concept_canonical"]
    assert canonical["canonical_id"] == "concept_canonical"
    assert canonical["unresolved"] is False
    assert canonical["tag_video_count"] == 1
    assert canonical["connection_video_count"] == 1

    endpoint_only = by_id["c-connection-only-1ea0cc57"]
    assert endpoint_only["tag_video_count"] == 0
    assert endpoint_only["connection_video_count"] == 1
    assert endpoint_only["unresolved"] is True
    assert any(
        node["id"] == "c-unknown-legacy-f089cb4a"
        for node in graph["nodes"]
    )
    assert all(edge["source"] in by_id and edge["target"] in by_id for edge in graph["edges"])


def test_reversing_canonical_graph_inputs_is_byte_identical() -> None:
    loaded = load_corpus(CONSUMER_FIXTURE)
    corpus = compile_corpus(loaded.videos, overlay_root=CONSUMER_FIXTURE)
    reversed_videos = tuple(
        replace(video, connections=tuple(reversed(video.connections)))
        for video in reversed(corpus.videos)
    )
    reversed_corpus = replace(corpus, videos=reversed_videos)

    assert json_text(build_concept_graph(corpus)) == json_text(
        build_concept_graph(reversed_corpus)
    )


def test_legacy_graph_tie_breaks_do_not_depend_on_caller_order() -> None:
    nodes = concepts(["beta", "alpha"])
    videos = [
        {
            "video_id": "b",
            "connections": [
                {
                    "concept_id": "c-0",
                    "connects_to_id": "c-1",
                    "relationship": "same",
                }
            ],
        }
    ]
    reversed_videos = list(reversed(videos))

    first = build_concept_graph(nodes, videos)
    second = build_concept_graph(list(reversed(nodes)), reversed_videos)

    assert json_text(first) == json_text(second)
