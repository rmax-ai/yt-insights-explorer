"""Typed input models used between loading and normalization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

INSIGHT_SECTIONS = (
    "core_insights",
    "deep_dives",
    "article_ideas",
    "project_ideas",
    "architectural_implications",
    "tradeoffs_and_failure_modes",
    "open_questions",
    "key_claims",
    "connections",
)
INSIGHT_TYPES = (
    "architecture",
    "mechanism",
    "mental_model",
    "practice",
    "empirical_result",
    "failure_mode",
    "prediction",
    "tradeoff",
)
EVIDENCE_STRENGTHS = ("weak", "moderate", "strong")
NOVELTIES = ("low", "medium", "high")
PRIORITIES = ("low", "medium", "high")
CLAIM_TYPES = ("causal", "comparative", "factual", "opinion", "prediction")
PROJECT_FITS = ("beyond-evals", "gatehouse", "movement-lab", "new")
INDEX_STATUSES = ("analyzed", "skipped", "failed")


@dataclass(frozen=True)
class IndexItem:
    video_id: str
    title: str
    channel: str
    status: str
    ingested_at: datetime
    artifact_summary: str | None
    artifact_insights: str | None
    cost_usd_total: float | None


@dataclass(frozen=True)
class RawVideo:
    index: IndexItem
    frontmatter: dict[str, Any]
    summary_markdown: str
    insights: dict[str, Any]
    summary_path: str
    insights_path: str

    @property
    def video_id(self) -> str:
        return self.index.video_id

    @property
    def title(self) -> str:
        return self.index.title

    @property
    def channel(self) -> str:
        return self.index.channel


@dataclass(frozen=True)
class LoadedCorpus:
    source_root: Path
    index_items: tuple[IndexItem, ...]
    videos: tuple[RawVideo, ...]
    warnings: tuple[str, ...]
