"""Transactional static-site build orchestration."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .derive import derive_claims, derive_ideas, derive_trends
from .graph import build_concept_graph
from .load import CorpusValidationError, load_corpus
from .normalize import NormalizedCorpus, normalize_corpus
from .render import RenderConfig, render_site
from .search import build_search_records
from .serialize import json_text
from .verify import VerificationError, verify_site


class BuildError(RuntimeError):
    """Raised when a build cannot safely produce a complete output tree."""


def _overlaps(source: Path, output: Path) -> bool:
    try:
        output.relative_to(source)
        return True
    except ValueError:
        pass
    try:
        source.relative_to(output)
        return True
    except ValueError:
        return False


def _counts(
    corpus: NormalizedCorpus, ideas: dict[str, Any], claims: dict[str, Any]
) -> dict[str, Any]:
    index_items = corpus.index_items
    return {
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
    }


def _data_files(corpus: NormalizedCorpus, config: RenderConfig) -> dict[str, Any]:
    ideas = derive_ideas(corpus.videos)
    claims = derive_claims(corpus.videos)
    trends = derive_trends(corpus.videos)
    concept_graph = build_concept_graph(corpus.concepts, corpus.videos)
    counts = _counts(corpus, ideas, claims)
    ordered_videos = sorted(
        corpus.videos,
        key=lambda video: video["video_id"],
    )
    ordered_videos = sorted(
        ordered_videos,
        key=lambda video: video["source"]["published_at"],
        reverse=True,
    )
    video_ids = [video["video_id"] for video in ordered_videos]
    costs = [
        item["cost_usd_total"] for item in corpus.index_items if item["cost_usd_total"] is not None
    ]
    corpus_data: dict[str, Any] = {
        "schema_version": 1,
        "site": {
            "title": config.site_title,
            "publication_mode": config.publication_mode,
            "base_path": config.base_path,
        },
        "counts": counts,
        "total_cost_usd": sum(costs),
        "video_ids": video_ids,
        "months": sorted({video["source"]["published_month"] for video in corpus.videos}),
    }
    if config.generated_at is not None:
        corpus_data["site"]["generated_at"] = config.generated_at
    files: dict[str, Any] = {
        "data/corpus.json": corpus_data,
        "data/trends.json": trends,
        "data/concepts.json": concept_graph,
        "data/ideas.json": ideas,
        "data/claims.json": claims,
        "data/search.json": build_search_records(corpus.videos, corpus.concepts),
    }
    for video in corpus.videos:
        files[f"data/videos/{video['video_id']}.json"] = video
    return files


def _write_tree(root: Path, rendered: dict[str, str], data: dict[str, Any]) -> None:
    for relative, content in sorted(rendered.items()):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content.replace("\r\n", "\n"), encoding="utf-8", newline="\n")
    for relative, value in sorted(data.items()):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json_text(value), encoding="utf-8", newline="\n")


def _basic_validate_output(root: Path) -> None:
    try:
        verify_site(root)
    except VerificationError as exc:
        raise BuildError(str(exc)) from exc


def build_site(
    source: str | Path,
    output: str | Path = "site",
    *,
    site_title: str = "YT Insights Explorer",
    base_path: str = "./",
    publication_mode: str = "private",
    acknowledge_private_unreviewed: bool = False,
    generated_at: str | None = None,
) -> Path:
    """Build into a sibling temporary directory and atomically replace output."""

    source_root = Path(source).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    if _overlaps(source_root, output_path):
        raise BuildError("source and output paths overlap")
    if publication_mode not in {"private", "public"}:
        raise BuildError(f"unknown publication mode: {publication_mode}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        loaded = load_corpus(source_root)
        normalized = normalize_corpus(loaded)
    except CorpusValidationError as exc:
        raise BuildError(str(exc)) from exc
    if publication_mode == "public" and not acknowledge_private_unreviewed:
        if any(
            video["document"]["visibility"] == "private"
            or video["document"]["review_status"] == "unreviewed"
            for video in normalized.videos
        ):
            raise BuildError(
                "public mode requires --acknowledge-private-unreviewed while "
                "included records remain private or unreviewed"
            )

    config = RenderConfig(
        site_title=site_title,
        base_path=base_path,
        publication_mode=publication_mode,
        generated_at=generated_at,
    )
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_path.name}.tmp-", dir=output_path.parent))
    backup: Path | None = None
    try:
        rendered = render_site(normalized, config)
        data = _data_files(normalized, config)
        _write_tree(temporary, rendered, data)
        _basic_validate_output(temporary)
        if output_path.exists():
            backup = output_path.parent / f".{output_path.name}.backup-{os.getpid()}"
            if backup.exists():
                shutil.rmtree(backup)
            os.replace(output_path, backup)
        os.replace(temporary, output_path)
        temporary = Path()
        if backup is not None and backup.exists():
            shutil.rmtree(backup)
            backup = None
    except Exception as exc:
        if backup is not None and backup.exists() and not output_path.exists():
            os.replace(backup, output_path)
            backup = None
        if isinstance(exc, BuildError):
            raise
        raise BuildError(f"build failed: {exc}") from exc
    finally:
        if temporary != Path() and temporary.exists():
            shutil.rmtree(temporary)
        if backup is not None and backup.exists():
            shutil.rmtree(backup)
    return output_path
