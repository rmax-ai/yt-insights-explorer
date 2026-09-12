from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from yt_insights_web.corpus.compiler import compile_corpus
from yt_insights_web.corpus.identity import claim_fingerprint
from yt_insights_web.corpus.ledgers import (
    ClaimLedger,
    ClaimLedgerValidationError,
)
from yt_insights_web.corpus.registries import RegistryValidationError, load_registries
from yt_insights_web.load import load_corpus

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "v1-edge"
VIDEO_ID = "V1Edge9xYzA"
CLAIM_REF = f"claim:{VIDEO_ID}:key_claims:0"
CLAIM_TEXT = "A V1 baseline must preserve unavailable verification context."


def _documents(
    *,
    concepts: list[dict[str, object]] | None = None,
    claims: list[dict[str, object]] | None = None,
    policy: dict[str, object] | None = None,
) -> dict[str, object]:
    claim_document: dict[str, object] = {
        "schema_version": 1,
        "reviews": claims or [],
    }
    if policy is not None:
        claim_document["policy"] = policy
    return {
        "concepts": {"schema_version": 1, "concepts": concepts or []},
        "topics": {"schema_version": 1, "topics": []},
        "projects": {"schema_version": 1, "projects": []},
        "claims": claim_document,
    }


def _review(
    review_id: str,
    *,
    claim_refs: list[str] | None = None,
    fingerprint: str | None = None,
    status: str = "verified",
    supersedes: str | None = None,
    mixed_group: bool = False,
    reviewed_at: str = "2026-09-11T00:00:00Z",
) -> dict[str, object]:
    result: dict[str, object] = {
        "id": review_id,
        "claim_refs": claim_refs or [CLAIM_REF],
        "claim_fingerprint": fingerprint or claim_fingerprint(CLAIM_TEXT),
        "status": status,
        "method": "primary-source review",
        "evidence": [{"url": "https://example.test/source", "note": "checked"}],
        "reviewed_at": reviewed_at,
        "reviewer": "test reviewer",
    }
    if supersedes is not None:
        result["supersedes"] = supersedes
    if mixed_group:
        result["mixed_group"] = True
    return result


def _compiled(documents: dict[str, object]):
    loaded = load_corpus(FIXTURE)
    return compile_corpus(loaded.videos, overlays=documents)


def test_v1_claim_receives_explicit_review() -> None:
    compiled = _compiled(
        _documents(claims=[_review("review:v1-explicit")])
    )

    state = compiled.resolved_state
    assert state is not None
    claim = state.by_video_id[VIDEO_ID].claims[0]
    assert claim.occurrence_id == CLAIM_REF
    assert claim.status == "verified"
    assert claim.review_ids == ("review:v1-explicit",)


def test_equivalent_claims_retain_occurrence_identity() -> None:
    loaded = load_corpus(FIXTURE)
    compiled = compile_corpus(loaded.videos)
    first = compiled.videos[0]
    second = replace(first, video_id="second-video")
    fingerprint = first.key_claims[0].claim_fingerprint_value
    assert fingerprint is not None
    documents = _documents(
        claims=[
            _review(
                "review:equivalent",
                claim_refs=[
                    CLAIM_REF,
                    "claim:second-video:key_claims:0",
                ],
                fingerprint=fingerprint,
            )
        ]
    )

    result = compile_corpus((first, second), overlays=documents)

    claims = result.resolved_state.claims  # type: ignore[union-attr]
    assert [claim.occurrence_id for claim in claims] == [
        CLAIM_REF,
        "claim:second-video:key_claims:0",
    ]
    assert [claim.review_ids for claim in claims] == [
        ("review:equivalent",),
        ("review:equivalent",),
    ]


def test_review_fingerprint_mismatch_fails() -> None:
    with pytest.raises(ClaimLedgerValidationError) as error:
        _compiled(
            _documents(
                claims=[
                    _review(
                        "review:fingerprint-mismatch",
                        fingerprint=claim_fingerprint("a different claim"),
                    )
                ]
            )
        )

    message = str(error.value)
    assert "review:fingerprint-mismatch" in message
    assert CLAIM_REF in message


def test_conflicting_active_reviews_require_supersession() -> None:
    with pytest.raises(ClaimLedgerValidationError, match="explicit supersession"):
        _compiled(
            _documents(
                claims=[
                    _review("review:verified"),
                    _review("review:refuted", status="refuted"),
                ]
            )
        )


def test_retired_registry_entry_preserves_source_provenance() -> None:
    loaded = load_corpus(FIXTURE)
    compiled = compile_corpus(
        loaded.videos,
        overlays=_documents(
            concepts=[
                {
                    "id": "concept_multilingual",
                    "canonical_name": "多言語",
                    "aliases": [],
                    "status": "retired",
                }
            ]
        ),
    )

    state = compiled.resolved_state
    assert state is not None
    resolved = next(
        value
        for value in state.by_video_id[VIDEO_ID].concepts.labels
        if value.raw_label == "多言語"
    )
    assert resolved.resolved_id == "concept_multilingual"
    assert resolved.status == "retired"
    assert resolved.origin.value == "summary_frontmatter"
    assert resolved.location.endswith("summary.md::frontmatter.tags[1]")


@pytest.mark.parametrize("status", ["verified", "refuted", "stale", "superseded"])
def test_review_statuses_are_pinned(status: str) -> None:
    review = _review("review:status", status=status)
    ledger = ClaimLedger.from_registries(load_registries(documents=_documents(claims=[review])))
    assert ledger.reviews[0].status == status


def test_unknown_review_status_fails_closed() -> None:
    with pytest.raises(ClaimLedgerValidationError, match="unsupported review status"):
        _compiled(_documents(claims=[_review("review:unknown", status="pending")]))


def test_supersession_cycle_fails() -> None:
    with pytest.raises(ClaimLedgerValidationError, match="supersession cycle"):
        _compiled(
            _documents(
                claims=[
                    _review("review:a", supersedes="review:b"),
                    _review("review:b", supersedes="review:a"),
                ]
            )
        )


def test_duplicate_review_ids_fail_with_both_occurrences() -> None:
    reviews = [_review("review:duplicate"), _review("review:duplicate", status="refuted")]
    with pytest.raises(RegistryValidationError) as error:
        load_registries(documents=_documents(claims=reviews))

    message = str(error.value)
    assert "claim-verification.json::reviews[0]" in message
    assert "claim-verification.json::reviews[1]" in message


def test_policy_staleness_is_committed_data() -> None:
    compiled = _compiled(
        _documents(
            policy={"stale_after": "2026-09-12T00:00:00Z"},
            claims=[
                _review(
                    "review:policy-stale",
                    reviewed_at="2026-09-11T23:59:59Z",
                )
            ],
        )
    )
    assert compiled.resolved_state.claims[0].status == "stale"  # type: ignore[union-attr]


def test_build_clock_cannot_change_lifecycle_state(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    documents = _documents(
        policy={"stale_after": "2026-09-12T00:00:00Z"},
        claims=[_review("review:clock-independent", reviewed_at="2026-09-11T00:00:00Z")],
    )
    first = _compiled(documents)
    monkeypatch.setattr(time, "time", lambda: 0.0)
    second = _compiled(documents)
    assert first.resolved_state.claims == second.resolved_state.claims  # type: ignore[union-attr]


def test_mixed_group_must_be_explicit() -> None:
    loaded = load_corpus(FIXTURE)
    first = compile_corpus(loaded.videos).videos[0]
    second = replace(first, video_id="mixed-video")
    different = claim_fingerprint("a different claim")
    documents = _documents(
        claims=[
            _review(
                "review:mixed",
                claim_refs=[CLAIM_REF, "claim:mixed-video:key_claims:0"],
                fingerprint=different,
                mixed_group=True,
            )
        ]
    )

    compiled = compile_corpus((first, second), overlays=documents)
    assert compiled.resolved_state.claims[0].status == "verified"  # type: ignore[union-attr]


def test_claim_ledger_source_copy_is_read_only(tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(FIXTURE, source)
    corpus = source / "corpus"
    corpus.mkdir()
    (corpus / "claim-verification.json").write_text(
        json.dumps({"schema_version": 1, "reviews": [_review("review:read-only")]}),
        encoding="utf-8",
    )
    before = {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file()
    }
    compile_corpus(load_corpus(source).videos, overlays=source)
    after = {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file()
    }
    assert before == after
