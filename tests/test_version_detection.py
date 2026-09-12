"""Public per-artifact version detection and compiler-routing contract tests.

Each machine-readable artifact chooses its own schema version.  A missing
``schema_version`` is the legacy V1 format, while a present unsupported
version must be rejected before V1 validation.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from yt_insights_web.build import BuildError, build_site
from yt_insights_web.corpus.source_models import SourceVersion, V1SourceRecord
from yt_insights_web.load import CorpusValidationError, load_corpus

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "v1-edge"
INSIGHTS_RELATIVE = Path("artifacts/v1-edge-video/insights.json")


def copy_fixture(tmp_path: Path) -> Path:
    destination = tmp_path / "corpus"
    shutil.copytree(FIXTURE, destination)
    return destination


def _insights_path(source: Path) -> Path:
    return source / INSIGHTS_RELATIVE


def _set_artifact_paths_absolute(source: Path) -> None:
    index_path = source / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    for item in index["items"]:
        for artifact_name in ("summary", "insights"):
            relative = Path(item["artifacts"][artifact_name])
            item["artifacts"][artifact_name] = str((source / relative).resolve())
    index_path.write_text(json.dumps(index) + "\n", encoding="utf-8")


def _make_v2_shaped_fixture(tmp_path: Path) -> Path:
    source = copy_fixture(tmp_path)
    _insights_path(source).write_text(
        json.dumps(
            {
                "schema_version": 2,
                "artifact_kind": "video_insights",
                "video_id": "V1Edge9xYzA",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return source


def test_missing_schema_version_means_v1() -> None:
    source = json.loads(_insights_path(FIXTURE).read_text(encoding="utf-8"))

    assert "schema_version" not in source
    loaded = load_corpus(FIXTURE)

    assert len(loaded.videos) == 1
    assert isinstance(loaded.videos[0], V1SourceRecord)
    assert loaded.videos[0].schema_version is None
    assert loaded.videos[0].source_version is SourceVersion.V1


def test_explicit_schema_version_one_is_v1(tmp_path: Path) -> None:
    explicit_source = copy_fixture(tmp_path)
    insights_path = _insights_path(explicit_source)
    insights = json.loads(insights_path.read_text(encoding="utf-8"))
    insights["schema_version"] = 1
    insights_path.write_text(json.dumps(insights) + "\n", encoding="utf-8")

    implicit = load_corpus(FIXTURE)
    explicit = load_corpus(explicit_source)

    # Compare the parsed/adapted representation, not raw provenance spelling:
    # explicit V1 is canonicalized to the same representation as absent V1.
    assert explicit.index_items == implicit.index_items
    assert explicit.videos == implicit.videos
    assert explicit.videos[0].schema_version is None


@pytest.mark.parametrize("absolute_artifact_paths", [False, True])
def test_relative_and_absolute_artifact_paths_are_supported(
    tmp_path: Path, absolute_artifact_paths: bool
) -> None:
    source = copy_fixture(tmp_path)
    if absolute_artifact_paths:
        _set_artifact_paths_absolute(source)

    loaded = load_corpus(source)

    assert loaded.videos[0].summary_path == "artifacts/v1-edge-video/summary.md"
    assert loaded.videos[0].insights_path == "artifacts/v1-edge-video/insights.json"


def test_unsupported_v2_shaped_record_is_rejected_by_load(tmp_path: Path) -> None:
    source = _make_v2_shaped_fixture(tmp_path)

    with pytest.raises(
        CorpusValidationError,
        match=r"artifacts/v1-edge-video/insights\.json: unsupported schema version 2",
    ) as error:
        load_corpus(source)

    assert "core_insights" not in str(error.value)


def test_unsupported_v2_shaped_record_is_rejected_by_build(tmp_path: Path) -> None:
    source = _make_v2_shaped_fixture(tmp_path)

    with pytest.raises(
        BuildError,
        match=r"artifacts/v1-edge-video/insights\.json: unsupported schema version 2",
    ):
        build_site(source, tmp_path / "site")
