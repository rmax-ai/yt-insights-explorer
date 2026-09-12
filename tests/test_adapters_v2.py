from __future__ import annotations

import copy
import importlib
import json
import shutil
from pathlib import Path

import pytest

from yt_insights_web.corpus.adapters.v2 import (
    V2SourceRecord,
    V2ValidationError,
    adapt_v2,
    validate_v2_insights,
    validate_v2_summary,
)
from yt_insights_web.corpus.normalized_models import (
    EvidenceAvailability,
    IdentityKind,
)
from yt_insights_web.corpus.source_models import SourceVersion
from yt_insights_web.load import CorpusValidationError, load_corpus

ROOT = Path(__file__).resolve().parents[1]
V1_EDGE = ROOT / "tests" / "fixtures" / "v1-edge"
CONTRACT = ROOT / "tests" / "fixtures" / "v2-contract"
VIDEO_ID = "v2-contract-001"
V1_ID = "V1Edge9xYzA"


def _replace_strings(value: object, old: str, new: str) -> object:
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [_replace_strings(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: _replace_strings(item, old, new) for key, item in value.items()}
    return value


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _v2_source(
    tmp_path: Path,
    *,
    video_id: str = VIDEO_ID,
    summary_json: bool = True,
    insights: dict[str, object] | None = None,
    summary: dict[str, object] | None = None,
) -> Path:
    source = tmp_path / "source"
    shutil.copytree(V1_EDGE, source)
    index_path = source / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["items"][0]["video_id"] = video_id
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary_md = source / "artifacts" / "v1-edge-video" / "summary.md"
    summary_md.write_text(
        summary_md.read_text(encoding="utf-8").replace(V1_ID, video_id),
        encoding="utf-8",
    )
    insight_payload = (
        insights
        if insights is not None
        else json.loads((CONTRACT / "insights-golden.json").read_text(encoding="utf-8"))
    )
    summary_payload = (
        summary
        if summary is not None
        else json.loads((CONTRACT / "summary-golden.json").read_text(encoding="utf-8"))
    )
    insight_payload = _replace_strings(insight_payload, VIDEO_ID, video_id)
    summary_payload = _replace_strings(summary_payload, VIDEO_ID, video_id)
    insights_path = source / "artifacts" / "v1-edge-video" / "insights.json"
    _write_json(insights_path, insight_payload)
    summary_json_path = source / "artifacts" / "v1-edge-video" / "summary.json"
    if summary_json:
        _write_json(summary_json_path, summary_payload)
    else:
        summary_json_path.unlink(missing_ok=True)
    return source


def _payload(name: str) -> dict[str, object]:
    return json.loads((CONTRACT / name).read_text(encoding="utf-8"))


def test_loads_v2_by_sibling_summary_json_and_adapts_all_sections(tmp_path: Path) -> None:
    source = _v2_source(tmp_path)

    loaded = load_corpus(source)
    record = loaded.videos[0]
    normalized = adapt_v2(record)  # type: ignore[arg-type]

    assert isinstance(record, V2SourceRecord)
    assert record.source_version is SourceVersion.V2
    assert record.summary_json_path == "artifacts/v1-edge-video/summary.json"
    assert record.summary_json["artifact_kind"] == "video-summary"  # type: ignore[index]
    assert record.insights_json["artifact_kind"] == "video-insights"

    for section in (
        "core_insights",
        "deep_dives",
        "article_ideas",
        "project_ideas",
        "architectural_implications",
        "tradeoffs_and_failure_modes",
        "open_questions",
        "key_claims",
        "connections",
    ):
        values = getattr(normalized, section)
        assert values, section
        assert all(item.id_kind is IdentityKind.PERSISTED for item in values)
        assert all(item.provenance.version == 2 for item in values)

    core = normalized.core_insights[0]
    assert core.insight == (
        "System-level behavior cannot be inferred from isolated component scores alone."
    )
    assert core.type == "mental_model"
    assert core.why_it_matters.startswith("Teams can")
    assert core.generalization.startswith("Evaluate interactions")
    assert core.evidence_strength == "strong"
    assert core.novelty == "high"
    assert core.id == "item-v1:v2-contract-001:core-insight-1"

    project = normalized.project_ideas[0]
    assert project.raw_fit == "new"
    claim_types = {claim.claim_type for claim in normalized.key_claims}
    assert claim_types == {"factual", "opinion"}
    assert [claim.verification_requested for claim in normalized.key_claims] == [True, False]


def test_v2_evidence_refs_preserve_ids_timestamps_and_null_provenance(tmp_path: Path) -> None:
    source = _v2_source(tmp_path)
    record = load_corpus(source).videos[0]
    normalized = adapt_v2(record)  # type: ignore[arg-type]

    raw_evidence = record.insights_json["evidence"]
    assert len(raw_evidence) == 2
    assert all(entry["id"].startswith("evidence-v1:") for entry in raw_evidence)
    assert raw_evidence[0]["provenance"]["timestamp_method"] == "extracted-by-model"
    assert raw_evidence[1]["provenance"]["timestamp_method"] == "recovered-exact-match"

    core_evidence = normalized.core_insights[0].evidence[0]
    assert core_evidence.content_id == "evidence-v1:v2-contract-001:insight-source-a"
    assert core_evidence.timestamp_seconds == 12.5
    assert core_evidence.source_url.endswith("v2-contract-001")
    assert core_evidence.provenance.version == 2
    assert core_evidence.provenance.location.endswith("insights.json::evidence[0]")

    project_evidence = normalized.project_ideas[0]  # the item itself is evidence-ref backed
    assert project_evidence.name == "Interaction Failure Atlas"
    tradeoff_evidence = normalized.tradeoffs_and_failure_modes[0].evidence
    assert tradeoff_evidence.timestamp_seconds == 37
    assert tradeoff_evidence.timestamp_method == "recovered-exact-match"
    assert tradeoff_evidence.availability is EvidenceAvailability.RESOLVED_ENRICHMENT

    summary_evidence = record.summary_json["evidence"]  # type: ignore[index]
    assert summary_evidence[1]["timestamp_seconds"] is None
    assert summary_evidence[1]["provenance"]["timestamp_method"] == "none"
    assert summary_evidence[1]["source_url"] is None


def test_v2_topics_and_raw_concept_candidates_are_retained_as_labels(tmp_path: Path) -> None:
    record = load_corpus(_v2_source(tmp_path)).videos[0]

    assert [(item.label, item.origin) for item in record.insights.topics] == [
        ("systems thinking", "extraction"),
        ("evaluation", "extraction"),
    ]
    assert [(item.label, item.origin) for item in record.insights.concept_candidates] == [
        ("emergent behavior", "extraction")
    ]
    labels = adapt_v2(record).labels  # type: ignore[arg-type]
    assert [label.label for label in labels[-3:]] == [
        "systems thinking",
        "evaluation",
        "emergent behavior",
    ]


def test_reordering_v2_items_does_not_regenerate_persisted_ids(tmp_path: Path) -> None:
    insights = _payload("insights-golden.json")
    insights["key_claims"] = list(reversed(insights["key_claims"]))  # type: ignore[arg-type]
    source = _v2_source(tmp_path, insights=insights)

    record = load_corpus(source).videos[0]
    normalized = adapt_v2(record)  # type: ignore[arg-type]

    assert {claim.id for claim in normalized.key_claims} == {
        "claim-v1:v2-contract-001:key-claim-1",
        "claim-v1:v2-contract-001:key-claim-2",
    }
    assert normalized.key_claims[0].verification_requested is False
    assert normalized.key_claims[1].verification_requested is True


@pytest.mark.parametrize(
    ("filename", "validator"),
    [
        ("duplicate-ids.json", validate_v2_summary),
        ("dangling-evidence-ref.json", validate_v2_insights),
        ("schema-version-mismatch.json", validate_v2_summary),
    ],
)
def test_vendored_negative_fixtures_fail_closed(filename: str, validator) -> None:
    payload = json.loads((CONTRACT / "invalid" / filename).read_text(encoding="utf-8"))

    with pytest.raises(V2ValidationError):
        validator(payload, path=f"v2-contract/{filename}")


def test_version_two_compatibility_fields_never_reach_v1_validator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _v2_source(tmp_path, summary_json=False)
    insights_path = source / "artifacts" / "v1-edge-video" / "insights.json"
    payload = json.loads(insights_path.read_text(encoding="utf-8"))
    payload = {
        "schema_version": 2,
        "artifact_kind": "video-insights",
        "video_id": VIDEO_ID,
        **{
            key: value
            for key, value in payload.items()
            if key in {
                "core_insights",
                "deep_dives",
                "article_ideas",
                "project_ideas",
                "architectural_implications",
                "tradeoffs_and_failure_modes",
                "open_questions",
                "key_claims",
                "connections",
                "tags",
            }
        },
    }
    insights_path.write_text(json.dumps(payload), encoding="utf-8")

    load_module = importlib.import_module("yt_insights_web.load")

    def fail_if_called(*args: object, **kwargs: object) -> object:
        raise AssertionError("V2 payload reached the V1 validator")

    monkeypatch.setattr(load_module, "_validate_insights", fail_if_called)
    with pytest.raises(CorpusValidationError, match="evidence"):
        load_corpus(source)


def test_missing_evidence_id_is_rejected(tmp_path: Path) -> None:
    payload = _payload("insights-golden.json")
    payload["evidence"][0].pop("id")  # type: ignore[index]

    with pytest.raises(V2ValidationError, match="evidence\\[0\\].id"):
        validate_v2_insights(payload)


def test_v1_summary_md_paired_with_v2_insights_is_valid(tmp_path: Path) -> None:
    loaded = load_corpus(_v2_source(tmp_path, summary_json=False))

    assert len(loaded.videos) == 1
    assert loaded.videos[0].source_version is SourceVersion.V2
    assert loaded.videos[0].summary_json is None  # type: ignore[attr-defined]


def test_summary_json_and_v1_insights_are_a_version_contradiction(tmp_path: Path) -> None:
    source = _v2_source(tmp_path)
    insights_path = source / "artifacts" / "v1-edge-video" / "insights.json"
    original = json.loads((V1_EDGE / "artifacts" / "v1-edge-video" / "insights.json").read_text())
    insights_path.write_text(json.dumps(original), encoding="utf-8")

    with pytest.raises(CorpusValidationError, match="schema_version 1 contradicts"):
        load_corpus(source)


def test_sibling_summary_video_id_mismatch_is_structured(tmp_path: Path) -> None:
    source = _v2_source(tmp_path)
    summary_path = source / "artifacts" / "v1-edge-video" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["video_id"] = "v2-contract-other"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    with pytest.raises(CorpusValidationError) as error:
        load_corpus(source)

    issue = next(issue for issue in error.value.issues if issue.code == "invalid_v2_artifact")
    assert issue.video_id == VIDEO_ID
    assert issue.artifact == "artifacts/v1-edge-video/summary.json"
    assert issue.schema_version == 2
    assert "video_id" in issue.path


def test_malformed_json_and_missing_metadata_are_explicit(tmp_path: Path) -> None:
    source = _v2_source(tmp_path)
    summary_path = source / "artifacts" / "v1-edge-video" / "summary.json"
    summary_path.write_text("{not-json\n", encoding="utf-8")

    with pytest.raises(CorpusValidationError) as error:
        load_corpus(source)
    invalid = next(issue for issue in error.value.issues if issue.code == "invalid_json")
    assert invalid.schema_version == "unknown"
    assert invalid.artifact == "artifacts/v1-edge-video/summary.json"

    source = _v2_source(tmp_path / "missing")
    summary_path = source / "artifacts" / "v1-edge-video" / "summary.json"
    summary_path.write_text("null\n", encoding="utf-8")
    with pytest.raises(CorpusValidationError) as error:
        load_corpus(source)
    missing = next(issue for issue in error.value.issues if issue.code == "missing_metadata")
    assert missing.schema_version == "unknown"
    assert missing.path.endswith("summary.json")


def test_unsupported_schema_version_names_path_version_and_field(tmp_path: Path) -> None:
    source = _v2_source(tmp_path)
    summary_path = source / "artifacts" / "v1-edge-video" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["schema_version"] = 3
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    with pytest.raises(CorpusValidationError) as error:
        load_corpus(source)

    issue = next(
        issue
        for issue in error.value.issues
        if issue.code == "unsupported_schema_version"
    )
    assert issue.path == "artifacts/v1-edge-video/summary.json"
    assert issue.schema_version == 3
    assert issue.video_id == VIDEO_ID
    assert issue.artifact == "artifacts/v1-edge-video/summary.json"


def test_duplicate_evidence_ids_across_videos_are_rejected(tmp_path: Path) -> None:
    source = _v2_source(tmp_path)
    first_insights = json.loads(
        (source / "artifacts" / "v1-edge-video" / "insights.json").read_text()
    )
    first_evidence_id = first_insights["evidence"][0]["id"]
    second_dir = source / "artifacts" / "v2-second"
    first_dir = source / "artifacts" / "v1-edge-video"
    shutil.copytree(first_dir, second_dir)
    second_id = "v2-contract-002"
    second_summary_md = second_dir / "summary.md"
    second_summary_md.write_text(
        second_summary_md.read_text(encoding="utf-8").replace(VIDEO_ID, second_id),
        encoding="utf-8",
    )
    for name in ("summary.json", "insights.json"):
        path = second_dir / name
        data = _replace_strings(json.loads(path.read_text(encoding="utf-8")), VIDEO_ID, second_id)
        if name == "insights.json":
            data["evidence"][0]["id"] = first_evidence_id  # type: ignore[index]
        _write_json(path, data)
    index_path = source / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    second_item = copy.deepcopy(index["items"][0])
    second_item["video_id"] = second_id
    second_item["artifacts"]["summary"] = "artifacts/v2-second/summary.md"
    second_item["artifacts"]["insights"] = "artifacts/v2-second/insights.json"
    index["items"].append(second_item)
    _write_json(index_path, index)

    with pytest.raises(CorpusValidationError, match="duplicate evidence ID"):
        load_corpus(source)


def test_cross_video_evidence_reference_is_rejected(tmp_path: Path) -> None:
    source = _v2_source(tmp_path)
    payload = json.loads(
        (source / "artifacts" / "v1-edge-video" / "insights.json").read_text()
    )
    payload["core_insights"][0]["evidence_refs"][0] = (
        "evidence-v1:v2-contract-other:source-quote-a"
    )
    (source / "artifacts" / "v1-edge-video" / "insights.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    with pytest.raises(CorpusValidationError, match="does not match video_id|does not resolve"):
        load_corpus(source)
