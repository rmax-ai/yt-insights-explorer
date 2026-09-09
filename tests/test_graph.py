from __future__ import annotations

from yt_insights_web.graph import build_concept_graph


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
