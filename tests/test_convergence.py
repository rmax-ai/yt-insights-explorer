from __future__ import annotations

import json
from pathlib import Path

from yt_insights_web.build import build_site
from yt_insights_web.derive import derive_convergence
from yt_insights_web.serialize import json_text
from yt_insights_web.verify import verify_site

ROOT = Path(__file__).resolve().parents[1]
V1_FIXTURE = ROOT / "tests" / "fixtures" / "v1-edge"
CONVERGENCE_FIXTURE = ROOT / "tests" / "fixtures" / "convergence"


def video(
    video_id: str,
    tags: list[str],
    insights: list[tuple[str, str]] | None = None,
) -> dict:
    return {
        "video_id": video_id,
        "title": f"Video {video_id}",
        "channel": "Channel",
        "status": "analyzed",
        "url": f"videos/{video_id}/index.html",
        "source": {
            "published_at": "2026-01-15T00:00:00Z",
            "published_date": "2026-01-15",
            "published_month": "2026-01",
        },
        "tags": [
            {"name": name, "concept_id": f"c-{name}", "url": f"concepts/{name}/index.html"}
            for name in tags
        ],
        "core_insights": [
            {
                "id": f"{video_id}:core_insights:{index}",
                "insight": "Insight",
                "type": "architecture",
                "why_it_matters": "Because",
                "generalization": "General",
                "evidence_quotes": [],
                "evidence_strength": strength,
                "novelty": novelty,
            }
            for index, (novelty, strength) in enumerate(insights or [])
        ],
    }


def node(
    node_id: str,
    name: str,
    video_ids: list[str],
    *,
    tag_video_count: int = 0,
    connection_video_count: int = 0,
) -> dict:
    return {
        "id": node_id,
        "name": name,
        "slug": node_id,
        "url": f"concepts/{node_id}/index.html",
        "video_ids": video_ids,
        "tag_video_count": tag_video_count,
        "connection_video_count": connection_video_count,
        "degree": 0,
        "x": 0,
        "y": 0,
    }


def graph(nodes: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "nodes": nodes,
        "edges": [],
        "graph": {
            "width": 1200,
            "height": 800,
            "display_node_ids": [],
            "display_edge_ids": [],
        },
    }


def variant_graph() -> dict:
    """Two nodes that differ only in case and separators, plus one singleton."""

    return graph(
        [
            node(
                "c-Context-engineering",
                "Context engineering",
                ["v1", "v2"],
                tag_video_count=2,
                connection_video_count=2,
            ),
            node(
                "c-context-engineering",
                "context-engineering",
                ["v2", "v3", "v4"],
                tag_video_count=4,
            ),
            node("c-lonely", "Lonely concept", ["v9"]),
        ]
    )


def variant_videos() -> list[dict]:
    return [
        video("v1", ["context-engineering"], [("high", "strong")]),
        video("v2", ["context-engineering", "harness"], [("low", "weak")]),
        video("v3", ["harness"], [("high", "weak"), ("medium", "strong")]),
        video("v4", ["harness"], [("high", "strong")]),
        video("v9", ["lonely"], [("high", "strong")]),
    ]


def test_convergence_merges_case_and_punctuation_variants() -> None:
    result = derive_convergence(variant_videos(), variant_graph())

    assert [group["key"] for group in result["groups"]] == ["contextengineering"]
    group = result["groups"][0]
    assert group["support"] == 4
    assert group["label"] == "context-engineering"
    assert group["video_ids"] == ["v1", "v2", "v3", "v4"]
    assert [member["name"] for member in group["members"]] == [
        "context-engineering",
        "Context engineering",
    ]


def test_convergence_excludes_singletons_and_reports_recovered_support() -> None:
    result = derive_convergence(variant_videos(), variant_graph())

    assert result["min_support"] == 2
    assert all(group["support"] >= result["min_support"] for group in result["groups"])
    assert "lonely concept" not in json_text(result).casefold()
    assert result["totals"]["groups"] == 1
    assert result["totals"]["nodes_grouped"] == 2
    assert result["totals"]["support_recovered"] == 1


def test_convergence_counts_novelty_and_evidence_across_supporting_videos() -> None:
    result = derive_convergence(variant_videos(), variant_graph())
    group = result["groups"][0]

    assert group["insight_total"] == 5
    assert group["high_novelty"] == 3
    assert group["gold"] == 2
    assert group["top_tags"][0] == {"name": "harness", "videos": 3}


def test_convergence_output_is_deterministic() -> None:
    first = derive_convergence(variant_videos(), variant_graph())
    second = derive_convergence(
        list(reversed(variant_videos())),
        graph(list(reversed(variant_graph()["nodes"]))),
    )

    assert json_text(first) == json_text(second)


def test_convergence_ranks_groups_by_support_then_gold() -> None:
    nodes = [
        node("c-alpha", "alpha-rig", ["v1", "v2"]),
        node("c-alpha-two", "alpha rig", ["v3"]),
        node("c-beta", "beta-loop", ["v1", "v2", "v3", "v4"], tag_video_count=4),
        node("c-gamma", "gamma-loop", ["v5", "v6"]),
        node("c-delta", "delta-loop", ["v7", "v8"]),
    ]
    videos = [
        video("v1", ["alpha"], [("high", "strong")]),
        video("v2", ["beta"], [("high", "strong")]),
        video("v3", ["beta"], []),
        video("v4", ["beta"], []),
        video("v5", ["gamma"], [("high", "strong")]),
        video("v6", ["gamma"], []),
        video("v7", ["delta"], []),
        video("v8", ["delta"], []),
    ]
    result = derive_convergence(videos, graph(nodes))

    # Highest support first; equal support falls back to the gold insight count.
    assert [group["label"] for group in result["groups"]] == [
        "beta-loop",
        "alpha-rig",
        "gamma-loop",
        "delta-loop",
    ]


def test_data_contract_documents_the_convergence_file() -> None:
    text = (ROOT / "docs" / "data-contract.md").read_text(encoding="utf-8")

    assert "data/convergence.json" in text
    assert "min_support" in text
    assert "casefolding" in text


def test_v1_fixture_build_omits_the_convergence_surface(tmp_path: Path) -> None:
    output = tmp_path / "site"
    build_site(V1_FIXTURE, output, generated_at=None)

    assert not (output / "data" / "convergence.json").exists()
    assert not (output / "convergence" / "index.html").exists()


def test_build_emits_convergence_page_and_data(tmp_path: Path) -> None:
    output = tmp_path / "site"
    build_site(CONVERGENCE_FIXTURE, output, generated_at=None)

    page = output / "convergence" / "index.html"
    data = json.loads((output / "data" / "convergence.json").read_text(encoding="utf-8"))

    assert page.is_file()
    assert data["totals"]["groups"] == 1
    group = data["groups"][0]
    assert group["label"] == "systems thinking"
    assert group["support"] == 2
    assert [member["name"] for member in group["members"]] == [
        "systems thinking",
        "systems-thinking",
    ]
    assert "systems thinking" in page.read_text(encoding="utf-8")
    assert "convergence/index.html" in (output / "index.html").read_text(encoding="utf-8")
    verify_site(output)
