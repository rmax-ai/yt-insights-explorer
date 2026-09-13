"""Render all static pages and inline visualizations from normalized data."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC
from html import escape
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .corpus.compiler import OverlayState
from .corpus.normalized_models import (
    Evidence,
    NormalizedVideo,
)
from .corpus.normalized_models import NormalizedCorpus as CanonicalCorpus
from .corpus.registries import Registries
from .derive import derive_claims, derive_ideas, derive_trends, month_axis
from .frontmatter import render_markdown
from .graph import build_concept_graph
from .models import CLAIM_TYPES, INSIGHT_TYPES, PROJECT_FITS
from .normalize import NormalizedCorpus
from .search import KIND_ORDER, build_search_records
from .slug import concept_id as legacy_concept_id
from .slug import video_slug
from .urls import asset_url, normalize_base_path, page_url, relative_url


@dataclass(frozen=True)
class RenderConfig:
    site_title: str = "YT Insights Explorer"
    base_path: str = "./"
    publication_mode: str = "private"
    generated_at: str | None = None


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    path: str
    glyph: str


NAV_ITEMS = (
    NavItem("home", "Home", "index.html", "H"),
    NavItem("trends", "Trends", "trends/index.html", "T"),
    NavItem("concepts", "Concepts", "concepts/index.html", "C"),
    NavItem("ideas", "Ideas", "ideas/index.html", "I"),
    NavItem("claims", "Claims", "claims/index.html", "!"),
    NavItem("videos", "Videos", "videos/index.html", "V"),
    NavItem("search", "Search", "search/index.html", "/"),
)

GLYPHS = {
    "architecture": "[A]",
    "mechanism": "[M]",
    "mental_model": "[O]",
    "practice": "[P]",
    "empirical_result": "[E]",
    "failure_mode": "[!]",
    "prediction": "[>]",
    "tradeoff": "[~]",
}
SECTION_LABELS = {
    "core_insights": "Core insights",
    "deep_dives": "Deep dives",
    "article_ideas": "Article ideas",
    "project_ideas": "Project ideas",
    "architectural_implications": "Architectural implications",
    "tradeoffs_and_failure_modes": "Tradeoffs and failure modes",
    "open_questions": "Open questions",
    "key_claims": "Key claims",
    "connections": "Connections",
}


def _timestamp_label(value: float | None) -> str | None:
    if value is None:
        return None
    return str(int(value)) if float(value).is_integer() else str(value)


def _canonical_evidence(value: Evidence) -> dict[str, Any]:
    """Project one canonical evidence value into the template contract."""

    return {
        "id": value.content_id,
        "text": value.text,
        "timestamp_seconds": value.timestamp_seconds,
        "timestamp_label": _timestamp_label(value.timestamp_seconds),
        # The compiler/evidence adapter owns this URL.  Rendering only
        # consumes it verbatim and never appends a timestamp parameter.
        "source_url": value.source_url,
        "availability": value.availability.value,
        "resolution_method": value.resolution_method,
    }


def _canonical_source(
    corpus: CanonicalCorpus,
    video: NormalizedVideo,
) -> tuple[Registries, Any]:
    state = corpus.overlay_state if isinstance(corpus.overlay_state, OverlayState) else None
    if state is not None:
        return state.registries, state.by_video_id[video.video_id].concepts
    registries = Registries()
    return registries, registries.resolve_source_record(video)


def _canonical_video_view(
    corpus: CanonicalCorpus,
    video: NormalizedVideo,
    claim_rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build the renderer view for a canonical video without changing models."""

    registries, resolved_source = _canonical_source(corpus, video)
    del registries
    index_item = next(
        (item for item in corpus.index_items if item.video_id == video.video_id),
        None,
    )
    published_at = video.source.published_at.astimezone(UTC)
    view: dict[str, Any] = {
        "schema_version": 1,
        "video_id": video.video_id,
        "slug": video_slug(video.title, video.video_id),
        "url": f"videos/{video_slug(video.title, video.video_id)}/index.html",
        "title": video.title,
        "channel": video.channel,
        "status": video.status,
        "ingested_at": video.ingested_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "cost_usd_total": index_item.cost_usd_total if index_item is not None else None,
        "source": {
            "type": video.source.source_type,
            "uri": video.source.uri,
            "title": video.source.title,
            "author": video.source.author,
            "published_at": published_at.isoformat().replace("+00:00", "Z"),
            "published_date": published_at.date().isoformat(),
            "published_month": published_at.strftime("%Y-%m"),
        },
        "document": {
            "type": video.document.type,
            "description": video.document.description,
            "urn": video.document.urn,
            "status": video.document.status,
            "confidence": video.document.confidence,
            "visibility": video.document.visibility,
            "captured_at": video.document.captured_at.astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "generated_by": video.document.generated_by,
            "review_status": video.document.review_status,
        },
        "summary": {
            "markdown": video.summary.markdown,
            "html": render_markdown(video.summary.markdown),
        },
        "tags": [],
    }
    seen_tags: set[tuple[str, str]] = set()
    labels = resolved_source.labels
    if video.provenance.version == 2:
        labels = tuple(label for label in labels if "::topics[" not in label.location)
    for label in labels:
        tag_key = (label.id, label.name)
        if tag_key in seen_tags:
            continue
        seen_tags.add(tag_key)
        view["tags"].append(
            {
                "name": label.name,
                "concept_id": label.id,
                "url": label.url,
            }
        )

    view["core_insights"] = [
        {
            "id": item.id,
            "source_index": item.source_index,
            "insight": item.insight,
            "type": item.type,
            "why_it_matters": item.why_it_matters,
            "generalization": item.generalization,
            "evidence_quotes": [_canonical_evidence(value) for value in item.evidence],
            "evidence_strength": item.evidence_strength,
            "novelty": item.novelty,
        }
        for item in video.core_insights
    ]
    view["deep_dives"] = [
        {
            "id": item.id,
            "source_index": item.source_index,
            "topic": item.topic,
            "research_question": item.research_question,
            "why": item.why,
            "trigger_insight": item.trigger_insight,
            "evidence_quotes": [_canonical_evidence(value) for value in item.evidence],
            "priority": item.priority,
        }
        for item in video.deep_dives
    ]
    view["article_ideas"] = [
        {
            "id": item.id,
            "source_index": item.source_index,
            "title": item.title,
            "thesis": item.thesis,
            "angle": item.angle,
            "based_on": item.based_on,
            "audience": item.audience,
        }
        for item in video.article_ideas
    ]
    view["project_ideas"] = [
        {
            "id": item.id,
            "source_index": item.source_index,
            "name": item.name,
            "hypothesis": item.hypothesis,
            "poc": item.poc,
            "measurement": item.measurement,
            "based_on": item.based_on,
            "raw_fit": item.raw_fit,
            "fits": item.raw_fit,
        }
        for item in video.project_ideas
    ]
    view["architectural_implications"] = [
        {
            "id": item.id,
            "source_index": item.source_index,
            "observation": item.observation,
            "before": item.before,
            "after": item.after,
            "consequence": item.consequence,
        }
        for item in video.architectural_implications
    ]
    view["tradeoffs_and_failure_modes"] = [
        {
            "id": item.id,
            "source_index": item.source_index,
            "topic": item.topic,
            "benefit": item.benefit,
            "cost_or_risk": item.cost_or_risk,
            "evidence_quote": _canonical_evidence(item.evidence),
        }
        for item in video.tradeoffs_and_failure_modes
    ]
    view["open_questions"] = [
        {
            "id": item.id,
            "source_index": item.source_index,
            "question": item.question,
            "why_unresolved": item.why_unresolved,
            "research_direction": item.research_direction,
        }
        for item in video.open_questions
    ]
    view["key_claims"] = []
    for item in video.key_claims:
        claim = dict(claim_rows.get(item.id, {}))
        claim.update(
            {
                "id": item.id,
                "source_index": item.source_index,
                "claim": item.claim,
                "claim_type": item.claim_type,
                "evidence": _canonical_evidence(item.evidence),
                "verification_requested": item.verification_requested,
                "verification_needed": item.verification_requested,
                "verification_status": (
                    "needed" if item.verification_requested else "not-needed"
                ),
                "verification_question": item.verification_question,
                "review_status": claim.get("review_status") or "unreviewed",
                "ledger_review_state": claim.get("ledger_review_state") or "unreviewed",
            }
        )
        view["key_claims"].append(claim)
    view["connections"] = [
        {
            "id": item.id,
            "source_index": item.source_index,
            "concept": item.concept,
            "connects_to": item.connects_to,
            "relationship": item.relationship,
            "concept_id": resolved.concept.id,
            "connects_to_id": resolved.connects_to.id,
        }
        for item, resolved in zip(
            video.connections,
            resolved_source.connections,
            strict=True,
        )
    ]
    return view


def _canonical_counts(
    corpus: CanonicalCorpus,
    ideas: dict[str, Any],
    claims: dict[str, Any],
    concept_count: int,
) -> dict[str, Any]:
    costs = [
        item.cost_usd_total
        for item in corpus.index_items
        if item.cost_usd_total is not None
    ]
    return {
        "index_items": len(corpus.index_items),
        "analyzed_videos": len(corpus.videos),
        "skipped_videos": sum(item.status == "skipped" for item in corpus.index_items),
        "failed_videos": sum(item.status == "failed" for item in corpus.index_items),
        "concepts": concept_count,
        "core_insights": sum(len(video.core_insights) for video in corpus.videos),
        "article_ideas": len(ideas["article_ideas"]),
        "project_ideas": len(ideas["project_ideas"]),
        "deep_dives": len(ideas["deep_dives"]),
        "open_questions": len(ideas["open_questions"]),
        "claims": len(claims["claims"]),
        "claims_needed": sum(
            claim["verification_status"] == "needed" for claim in claims["claims"]
        ),
        "total_cost_usd": sum(costs),
    }


def _environment() -> Environment:
    templates = Path(__file__).with_name("templates")
    environment = Environment(
        loader=FileSystemLoader(templates),
        autoescape=select_autoescape(("html", "xml")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return environment


def _svg_line(values: list[int], width: int = 720, height: int = 220) -> str:
    margin_x, margin_y = 40, 24
    max_value = max(values, default=0)
    points = []
    if values:
        denominator = max(1, len(values) - 1)
        for index, value in enumerate(values):
            x = round(margin_x + (width - margin_x * 2) * index / denominator)
            y = round(height - margin_y - (height - margin_y * 2) * value / max(1, max_value))
            points.append((x, y))
    path = " ".join(f"{x},{y}" for x, y in points)
    start = values[0] if values else 0
    end = values[-1] if values else 0
    return (
        f'<svg class="chart-svg" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Corpus growth line from {start} to {end} videos">'
        f'<title>Corpus growth</title><path d="M {path}" fill="none" '
        'stroke="var(--color-chart-1)" stroke-width="3" stroke-linejoin="round"/>'
        f'<text x="{margin_x}" y="{height - 4}">start</text>'
        f'<text x="{width - margin_x}" y="{height - 4}" text-anchor="end">end</text>'
        f"</svg>"
    )


def _growth_svg(rows: list[dict[str, Any]]) -> str:
    return _svg_line([row["cumulative"] for row in rows])


def _insight_svg(rows: list[dict[str, Any]]) -> str:
    width, height = 720, 240
    margin_x, margin_y = 36, 24
    group_width = (width - margin_x * 2) / max(1, len(rows))
    max_total = max((row["total"] for row in rows), default=0)
    rects = []
    for index, row in enumerate(rows):
        current_y = height - margin_y
        for type_index, insight_type in enumerate(INSIGHT_TYPES):
            count = row["counts"][insight_type]
            bar_height = round((height - margin_y * 2) * count / max(1, max_total))
            if bar_height:
                current_y -= bar_height
                x = round(margin_x + index * group_width + 1)
                rects.append(
                    f'<rect x="{x}" y="{current_y}" width="{max(1, round(group_width - 2))}" '
                    f'height="{bar_height}" fill="var(--color-chart-{type_index + 1})">'
                    f"<title>{escape(row['month'])}: {escape(insight_type)} {count}</title></rect>"
                )
    labels = "".join(
        f'<text x="{round(margin_x + index * group_width + group_width / 2)}" y="{height - 4}" '
        f'text-anchor="middle">{escape(row["month"])}</text>'
        for index, row in enumerate(rows)
    )
    return (
        f'<svg class="chart-svg" viewBox="0 0 {width} {height}" role="img" '
        'aria-label="Stacked bars for insight types by publication month">'
        "<title>Insight types by month</title>" + "".join(rects) + labels + "</svg>"
    )


def _idea_svg(rows: list[dict[str, Any]]) -> str:
    width, height = 720, 240
    margin_x, margin_y = 36, 24
    keys = ("article_ideas", "project_ideas", "deep_dives", "open_questions")
    max_value = max((row[key] for row in rows for key in keys), default=0)
    group_width = (width - margin_x * 2) / max(1, len(rows))
    bar_width = max(2, group_width / (len(keys) + 1))
    rects = []
    for index, row in enumerate(rows):
        for key_index, key in enumerate(keys):
            value = row[key]
            x = round(margin_x + index * group_width + (key_index + 0.5) * bar_width)
            bar_height = round((height - margin_y * 2) * value / max(1, max_value))
            y = height - margin_y - bar_height
            width_value = max(1, round(bar_width - 1))
            title = f"{row['month']}: {key} {value}"
            rects.append(
                f'<rect x="{x}" y="{y}" width="{width_value}" height="{bar_height}" '
                f'fill="var(--color-chart-{key_index + 1})"><title>{escape(title)}</title></rect>'
            )
    labels = "".join(
        f'<text x="{round(margin_x + index * group_width + group_width / 2)}" y="{height - 4}" '
        f'text-anchor="middle">{escape(row["month"])}</text>'
        for index, row in enumerate(rows)
    )
    return (
        f'<svg class="chart-svg" viewBox="0 0 {width} {height}" role="img" '
        'aria-label="Grouped bars for ideas by publication month">'
        "<title>Idea flow by month</title>" + "".join(rects) + labels + "</svg>"
    )


def _concept_graph_svg(graph: dict[str, Any], concept_by_id: dict[str, dict[str, Any]], url) -> str:
    shown = set(graph["graph"]["display_node_ids"])
    display_edges = [
        edge for edge in graph["edges"] if edge["id"] in graph["graph"]["display_edge_ids"]
    ]
    display_nodes = [node for node in graph["nodes"] if node["id"] in shown]
    max_degree = max((node["degree"] for node in display_nodes), default=0)
    min_degree = min((node["degree"] for node in display_nodes), default=0)
    edge_chunks = []
    for edge in display_edges:
        source = concept_by_id[edge["source"]]
        target = concept_by_id[edge["target"]]
        relationship = escape(", ".join(edge["relationships"]))
        edge_chunks.append(
            f'<line class="graph-edge" x1="{source["x"]}" y1="{source["y"]}" '
            f'x2="{target["x"]}" y2="{target["y"]}"><title>{relationship}</title></line>'
        )
    edges = "".join(edge_chunks)
    nodes = []
    for node in sorted(display_nodes, key=lambda item: (item["name"].casefold(), item["id"])):
        radius = (
            8
            if max_degree == min_degree
            else round(5 + 9 * (node["degree"] - min_degree) / (max_degree - min_degree))
        )
        node_url = url(node["url"])
        node_label = escape(node["name"])
        node_label_attribute = escape(node["name"], quote=True)
        node_id = escape(node["id"], quote=True)
        nodes.append(
            f'<a href="{escape(node_url, quote=True)}" aria-label="{node_label_attribute}">'
            f'<circle class="graph-node" data-node-id="{node_id}" '
            f'cx="{node["x"]}" cy="{node["y"]}" r="{radius}"></circle>'
            f'<text class="graph-label" x="{node["x"] + radius + 3}" '
            f'y="{node["y"] + 4}">{node_label}</text></a>'
        )
    view_box = f"0 0 {graph['graph']['width']} {graph['graph']['height']}"
    return (
        f'<svg class="concept-graph-svg" viewBox="{view_box}" role="img" '
        'aria-label="Deterministic concept connection graph">'
        "<title>Concept connections</title>"
        f"{edges}{''.join(nodes)}</svg>"
    )


def _timeline_svg(timeline: list[dict[str, Any]]) -> str:
    return _svg_line([row["count"] for row in timeline])


def _counts(
    corpus: NormalizedCorpus, ideas: dict[str, Any], claims: dict[str, Any]
) -> dict[str, Any]:
    index_items = list(corpus.index_items)
    counts = {
        "index_items": len(index_items),
        "analyzed_videos": len(corpus.videos),
        "skipped_videos": sum(item["status"] == "skipped" for item in index_items),
        "failed_videos": sum(item["status"] == "failed" for item in index_items),
        "concepts": len(corpus.concepts),
        "core_insights": sum(len(video["core_insights"]) for video in corpus.videos),
        "article_ideas": len(ideas["article_ideas"]),
        "project_ideas": len(ideas["project_ideas"]),
        "deep_dives": len(ideas["deep_dives"]),
        "open_questions": len(ideas["open_questions"]),
        "claims": len(claims["claims"]),
        "claims_needed": sum(
            claim["verification_status"] == "needed" for claim in claims["claims"]
        ),
        "total_cost_usd": sum(
            item["cost_usd_total"] or 0
            for item in index_items
            if item["cost_usd_total"] is not None
        ),
    }
    return counts


def _concept_adjacency(
    graph: dict[str, Any], concepts: tuple[dict[str, Any], ...]
) -> list[dict[str, Any]]:
    concept_by_id = {concept["id"]: concept for concept in concepts}
    neighbor_map: dict[str, dict[str, set[str]]] = {concept["id"]: {} for concept in concepts}
    for edge in graph["edges"]:
        if edge["source"] not in neighbor_map:
            neighbor_map[edge["source"]] = {}
        if edge["target"] not in neighbor_map:
            neighbor_map[edge["target"]] = {}
        neighbor_map[edge["source"]].setdefault(edge["target"], set()).update(edge["relationships"])
        neighbor_map[edge["target"]].setdefault(edge["source"], set()).update(edge["relationships"])
    rows = []
    for concept in concepts:
        neighbors = [
            {
                **concept_by_id[target],
                "relationships": sorted(relationships, key=str.casefold),
            }
            for target, relationships in neighbor_map.get(concept["id"], {}).items()
            if target in concept_by_id
        ]
        rows.append(
            {**concept, "neighbors": sorted(neighbors, key=lambda item: item["name"].casefold())}
        )
    return rows


def _timeline_histogram(
    videos: tuple[dict[str, Any], ...],
) -> dict[str, dict[str, int]]:
    histogram: dict[str, dict[str, int]] = {}
    for video in videos:
        referenced_concepts = {tag["concept_id"] for tag in video["tags"]}
        referenced_concepts.update(
            endpoint
            for connection in video["connections"]
            for endpoint in (connection["concept_id"], connection["connects_to_id"])
        )
        month = video["source"]["published_month"]
        for concept_id in referenced_concepts:
            month_counts = histogram.setdefault(concept_id, {})
            month_counts[month] = month_counts.get(month, 0) + 1
    return histogram


def _timeline(
    concept_id: str,
    months: list[str],
    histogram: dict[str, dict[str, int]],
) -> list[dict[str, Any]]:
    counts = histogram.get(concept_id, {})
    rows = []
    for month in months:
        count = counts.get(month, 0)
        if count:
            rows.append({"month": month, "count": count})
    return rows


def render_site(
    corpus: NormalizedCorpus | CanonicalCorpus,
    config: RenderConfig | None = None,
    *,
    canonical: CanonicalCorpus | None = None,
) -> dict[str, str]:
    """Render pages and static assets into a relative-path string mapping."""

    config = config or RenderConfig()
    base_path = normalize_base_path(config.base_path)
    environment = _environment()
    compatibility_corpus = corpus if isinstance(corpus, NormalizedCorpus) else None
    canonical_corpus = canonical
    if isinstance(corpus, CanonicalCorpus):
        if canonical is not None:
            raise TypeError("canonical must be omitted when passing a canonical corpus")
        canonical_corpus = corpus
    use_canonical = canonical_corpus is not None and (
        compatibility_corpus is None
        or any(video.provenance.version != 1 for video in canonical_corpus.videos)
    )

    if use_canonical:
        if canonical_corpus is None:
            raise AssertionError("canonical corpus is required for canonical rendering")
        # Keep the non-evidence page shape compatible while the page-level
        # evidence and claim cards consume canonical rows below.
        trends = derive_trends(canonical_corpus, compatibility=True)
        ideas = derive_ideas(canonical_corpus, compatibility=True)
        claims = derive_claims(canonical_corpus, compatibility=False)
        graph = build_concept_graph(canonical_corpus, compatibility=True)
        claim_rows = {
            claim["id"]: claim
            for claim in claims["claims"]
        }
        claim_values = []
        for claim in claims["claims"]:
            value = dict(claim)
            value["verification_status"] = (
                "needed" if value["verification_requested"] else "not-needed"
            )
            value["review_status"] = value.get("review_status") or "unreviewed"
            value["ledger_review_state"] = (
                value.get("ledger_review_state") or value["review_status"]
            )
            claim_values.append(value)
        claims = {**claims, "claims": claim_values}
        views = tuple(
            _canonical_video_view(canonical_corpus, video, claim_rows)
            for video in canonical_corpus.videos
        )
        timeline_months = month_axis(list(canonical_corpus.videos))
        timeline_histogram = _timeline_histogram(views)
        counts = _canonical_counts(
            canonical_corpus,
            ideas,
            claims,
            len(graph["nodes"]),
        )
        videos_by_id = {video["video_id"]: video for video in views}
        concept_views = tuple(graph["nodes"])
        concept_urls = {concept["id"]: concept["url"] for concept in concept_views}
        concept_urls.update(
            {
                legacy_concept_id(concept["name"]): concept["url"]
                for concept in concept_views
            }
        )
        trends = {
            **trends,
            "tag_rankings": [
                item
                for item in trends["tag_rankings"]
                if item["concept_id"] in concept_urls
            ],
        }
        ideas = {
            **ideas,
            "project_ideas": [
                {**item, "fits": item.get("raw_fit", item.get("fits"))}
                for item in ideas["project_ideas"]
            ],
        }
        search_records = build_search_records(canonical_corpus)
    else:
        if compatibility_corpus is None:
            raise AssertionError("compatibility corpus is required for legacy rendering")
        trends = derive_trends(compatibility_corpus.videos)
        timeline_months = month_axis(list(compatibility_corpus.videos))
        timeline_histogram = _timeline_histogram(compatibility_corpus.videos)
        ideas = derive_ideas(compatibility_corpus.videos)
        claims = derive_claims(compatibility_corpus.videos)
        graph = build_concept_graph(compatibility_corpus.concepts, compatibility_corpus.videos)
        counts = _counts(compatibility_corpus, ideas, claims)
        videos_by_id = {video["video_id"]: video for video in compatibility_corpus.videos}
        concept_views = compatibility_corpus.concepts
        concept_urls = {concept["id"]: concept["url"] for concept in compatibility_corpus.concepts}
        search_records = build_search_records(
            compatibility_corpus.videos,
            compatibility_corpus.concepts,
        )
    views_by_id = videos_by_id
    concepts_by_id = {concept["id"]: concept for concept in graph["nodes"]}
    views = tuple(views_by_id.values())
    video_urls = {video["video_id"]: video["url"] for video in views}
    video_titles = {video["video_id"]: video["title"] for video in views}
    search_json = json.dumps(
        search_records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).replace("<", "\\u003c")

    def url_for(current_page: str, target: str) -> str:
        if target.startswith(("http://", "https://", "mailto:")):
            return target
        if base_path != "./":
            return page_url(target, base_path)
        return relative_url(current_page, target)

    def render_page(
        template_name: str, page_path: str, page_title: str, section: str, **values: Any
    ) -> None:
        context = {
            "site_title": config.site_title,
            "page_title": page_title,
            "robots": "noindex, nofollow"
            if config.publication_mode == "private"
            else "index, follow",
            "private_mode": config.publication_mode == "private",
            "publication_label": config.publication_mode,
            "base_path": base_path,
            "current_section": section,
            "nav_items": NAV_ITEMS,
            "url": lambda target: url_for(page_path, target),
            "css_href": asset_url(page_path, "assets/site.css", base_path),
            "js_href": asset_url(page_path, "assets/site.js", base_path),
            "glyphs": GLYPHS,
            "section_order": tuple(SECTION_LABELS),
            "section_labels": SECTION_LABELS,
            "concept_urls": concept_urls,
            "concept_url": concept_urls,
            "videos_by_id": videos_by_id,
            "canonical_render": use_canonical,
            **values,
        }
        output[page_path] = environment.get_template(template_name).render(context)

    output: dict[str, str] = {}
    if use_canonical:
        rising_concepts = [
            {**item, "url": concept_urls.get(item["concept_id"], item["url"])}
            for item in trends["tag_rankings"]
            if item["rising"]
        ][:8]
    else:
        rising_concepts = [
            {**item, **concepts_by_id[item["concept_id"]]}
            for item in trends["tag_rankings"]
            if item["rising"]
        ][:8]
    common = {
        "counts": counts,
        "latest_videos": list(views[:6]),
        "rising_concepts": rising_concepts,
        "growth_svg": _growth_svg(trends["corpus_growth"]),
    }
    render_page("home.html", "index.html", "Overview", "home", **common)
    recent_months = trends["months"][-min(3, len(trends["months"])) :] if trends["months"] else []
    prior_months = trends["months"][: -len(recent_months)][-3:] if recent_months else []
    render_page(
        "trends.html",
        "trends/index.html",
        "Trends",
        "trends",
        trends=trends,
        concept_urls=concept_urls,
        month_range=f"{trends['months'][0]} to {trends['months'][-1]}"
        if trends["months"]
        else "No months",
        recent_video_count=sum(
            row["published"] for row in trends["corpus_growth"] if row["month"] in recent_months
        ),
        prior_video_count=sum(
            row["published"] for row in trends["corpus_growth"] if row["month"] in prior_months
        ),
        insight_types=INSIGHT_TYPES,
        growth_svg=_growth_svg(trends["corpus_growth"]),
        insight_svg=_insight_svg(trends["insight_type_monthly"]),
        idea_svg=_idea_svg(trends["idea_flow_monthly"]),
    )
    render_page(
        "concepts.html",
        "concepts/index.html",
        "Concepts",
        "concepts",
        graph=graph,
        graph_svg=_concept_graph_svg(
            graph, concepts_by_id, lambda target: url_for("concepts/index.html", target)
        ),
        adjacency=_concept_adjacency(graph, concept_views),
    )
    idea_groups = (
        {"key": "article-ideas", "label": "Article ideas", "items": ideas["article_ideas"]},
        {"key": "project-ideas", "label": "Project ideas", "items": ideas["project_ideas"]},
        {"key": "deep-dives", "label": "Deep dives", "items": ideas["deep_dives"]},
        {"key": "open-questions", "label": "Open questions", "items": ideas["open_questions"]},
    )
    render_page(
        "ideas.html",
        "ideas/index.html",
        "Ideas",
        "ideas",
        idea_groups=idea_groups,
        project_fits=PROJECT_FITS,
    )
    render_page(
        "claims.html",
        "claims/index.html",
        "Claims",
        "claims",
        claims=claims["claims"],
        needed_count=sum(item["verification_status"] == "needed" for item in claims["claims"]),
        not_needed_count=sum(
            item["verification_status"] == "not-needed" for item in claims["claims"]
        ),
        claim_types=CLAIM_TYPES,
    )
    render_page(
        "videos.html",
        "videos/index.html",
        "Videos",
        "videos",
        videos=list(views),
    )
    render_page(
        "search.html",
        "search/index.html",
        "Search",
        "search",
        query="",
        search_json=search_json,
        search_kinds=tuple(KIND_ORDER),
    )
    render_page("404.html", "404.html", "Not found", "")

    for concept in concept_views:
        concept_video_ids = set(concept["video_ids"])
        concept_videos = tuple(
            video for video in views if video["video_id"] in concept_video_ids
        )
        tagged_videos = tuple(
            video
            for video in concept_videos
            if any(tag["concept_id"] == concept["id"] for tag in video["tags"])
        )
        neighbors = []
        for edge in graph["edges"]:
            if concept["id"] not in {edge["source"], edge["target"]}:
                continue
            other_id = edge["target"] if edge["source"] == concept["id"] else edge["source"]
            if other_id in concepts_by_id and other_id != concept["id"]:
                neighbors.append(
                    {
                        **concepts_by_id[other_id],
                        **{"relationships": edge["relationships"], "video_ids": edge["video_ids"]},
                    }
                )
        related = []
        for video in tagged_videos:
            tagged_insights = video["core_insights"]
            if tagged_insights:
                related.append((video, tagged_insights))
        timeline = _timeline(concept["id"], timeline_months, timeline_histogram)
        render_page(
            "concept_detail.html",
            f"concepts/{concept['slug']}/index.html",
            concept["name"],
            "concepts",
            concept=concept,
            videos=list(concept_videos),
            neighbors=sorted(neighbors, key=lambda item: item["name"].casefold()),
            related_insights=related,
            timeline=timeline,
            timeline_svg=_timeline_svg(timeline),
            video_urls=video_urls,
            video_titles=video_titles,
        )
    for video in views:
        render_page(
            "video.html",
            video["url"],
            video["title"],
            "videos",
            video=video,
        )

    css_path = Path(__file__).with_name("static") / "site.css"
    js_path = Path(__file__).with_name("static") / "site.js"
    output["assets/site.css"] = css_path.read_text(encoding="utf-8")
    output["assets/site.js"] = js_path.read_text(encoding="utf-8")
    return output
