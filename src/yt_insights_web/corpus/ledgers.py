"""Claim-review ledger validation and corpus-local application.

The ledger is an internal compiler overlay.  It validates committed review
metadata, binds occurrence references to adapted claims, and derives lifecycle
state without changing the legacy export projection.

Claim references are occurrence identities, not fingerprints::

    claim:<video_id>:<section>:<zero-based-index>

For the current V1 adapter, ``section`` is ``key_claims``.  Fingerprints are
checked as an integrity aid after references have been resolved.  The only
way a review may intentionally group claims with different fingerprints is
the review-local ``mixed_group: true`` flag.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from .normalized_models import NormalizedCorpus, NormalizedKeyClaim
from .registries import (
    ClaimPolicy,
    ClaimReview,
    OverlayValidationError,
    Registries,
    load_registries,
)

if TYPE_CHECKING:
    from pathlib import Path


ACTIVE_REVIEW_STATUSES = frozenset({"verified", "refuted"})
NON_ACTIVE_REVIEW_STATUSES = frozenset({"stale", "superseded"})
REVIEW_STATUSES = ACTIVE_REVIEW_STATUSES | NON_ACTIVE_REVIEW_STATUSES
UNREVIEWED_STATUS = "unreviewed"
CLAIM_SECTION = "key_claims"
CLAIM_REF_PATTERN = re.compile(r"^claim:(?P<video_id>[^:]+):(?P<section>[^:]+):(?P<index>[0-9]+)$")
CLAIM_FINGERPRINT_PATTERN = re.compile(r"^claim-fingerprint-v1:[0-9a-f]{64}$")


class ClaimLedgerValidationError(OverlayValidationError):
    """Raised when a claim ledger is structurally or referentially invalid."""


LedgerValidationError = ClaimLedgerValidationError
ClaimReviewValidationError = ClaimLedgerValidationError


def _error(message: str) -> ClaimLedgerValidationError:
    return ClaimLedgerValidationError(message)


def _location(review: ClaimReview) -> str:
    return review.location


def _parse_rfc3339(value: str, *, path: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise _error(f"{path}: must be a non-empty RFC3339 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _error(f"{path}: must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise _error(f"{path}: must include a timezone")
    return parsed


@dataclass(frozen=True, slots=True)
class ClaimOccurrence:
    """One adapted claim occurrence addressable by a ledger review."""

    ref: str
    video_id: str
    section: str
    index: int
    claim: NormalizedKeyClaim

    @property
    def claim_fingerprint(self) -> str | None:
        return self.claim.claim_fingerprint_value

    @property
    def fingerprint(self) -> str | None:
        return self.claim_fingerprint

    @property
    def location(self) -> str:
        return self.claim.provenance.location


@dataclass(frozen=True, slots=True)
class ReviewApplication:
    """One review after explicit supersession and policy are applied."""

    review: ClaimReview
    status: str
    superseded_by: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_REVIEW_STATUSES

    @property
    def non_active(self) -> bool:
        return not self.active

    @property
    def review_id(self) -> str:
        return self.review.id


@dataclass(frozen=True, slots=True)
class ClaimResolution:
    """Internal lifecycle state for one claim occurrence."""

    occurrence: ClaimOccurrence
    reviews: tuple[ReviewApplication, ...]
    status: str = UNREVIEWED_STATUS

    @property
    def ref(self) -> str:
        return self.occurrence.ref

    @property
    def occurrence_id(self) -> str:
        return self.ref

    @property
    def claim(self) -> NormalizedKeyClaim:
        return self.occurrence.claim

    @property
    def review_ids(self) -> tuple[str, ...]:
        return tuple(application.review.id for application in self.reviews)

    @property
    def active_reviews(self) -> tuple[ReviewApplication, ...]:
        return tuple(application for application in self.reviews if application.active)

    @property
    def review(self) -> ReviewApplication | None:
        """Return the single active review, or the first deterministic review."""

        active = self.active_reviews
        if active:
            return active[0]
        return self.reviews[0] if self.reviews else None

    @property
    def resolution_status(self) -> str:
        return self.status

    @property
    def review_status(self) -> str:
        return self.status

    @property
    def lifecycle_status(self) -> str:
        return self.status

    @property
    def state(self) -> str:
        return self.status

    @property
    def unresolved(self) -> bool:
        return self.status == UNREVIEWED_STATUS


@dataclass(frozen=True, slots=True)
class ClaimLedger:
    """Validated reviews plus deterministic corpus-local lifecycle bindings."""

    reviews: tuple[ClaimReview, ...] = ()
    policy: ClaimPolicy | None = None
    source_path: str | None = None

    def __post_init__(self) -> None:
        reviews = tuple(self.reviews)
        if any(not isinstance(review, ClaimReview) for review in reviews):
            raise _error("claim ledger reviews: must contain ClaimReview values")
        if self.policy is not None and not isinstance(self.policy, ClaimPolicy):
            raise _error("claim ledger policy: must be a ClaimPolicy or null")
        object.__setattr__(self, "reviews", tuple(sorted(reviews, key=lambda item: item.id)))
        self._validate_review_structure()

    @classmethod
    def from_registries(cls, registries: Registries) -> ClaimLedger:
        """Create a ledger from the E2-T2 overlay bundle."""

        if not isinstance(registries, Registries):
            raise TypeError("registries must be a Registries value")
        source_path = None
        if registries.overlay_root is not None:
            source_path = (
                registries.overlay_root / "corpus" / "claim-verification.json"
            ).as_posix()
        return cls(
            reviews=registries.claim_reviews,
            policy=registries.claim_policy,
            source_path=source_path,
        )

    @classmethod
    def from_documents(
        cls,
        documents: Mapping[str, object],
        *,
        overlay_root: str | Path | None = None,
    ) -> ClaimLedger:
        """Load a ledger from injected overlay documents for tests and tools."""

        if "reviews" in documents and "claims" not in documents:
            documents = {"claims": documents}
        registries = load_registries(documents=documents, overlay_root=overlay_root)
        return cls.from_registries(registries)

    def _validate_review_structure(self) -> None:
        errors: list[str] = []
        by_id: dict[str, ClaimReview] = {}
        for review in self.reviews:
            path = _location(review)
            if review.id in by_id:
                errors.append(
                    f"{path}: duplicate review ID {review.id!r}; "
                    f"first occurrence at {by_id[review.id].location}"
                )
            else:
                by_id[review.id] = review
            if review.status not in REVIEW_STATUSES:
                errors.append(
                    f"{path} (id={review.id!r}).status: unsupported review status "
                    f"{review.status!r}; expected one of {', '.join(sorted(REVIEW_STATUSES))}"
                )
            if (
                not isinstance(review.claim_fingerprint, str)
                or not CLAIM_FINGERPRINT_PATTERN.fullmatch(review.claim_fingerprint)
            ):
                errors.append(
                    f"{path} (id={review.id!r}).claim_fingerprint: must match "
                    "claim-fingerprint-v1:<64-hex>"
                )
            _parse_rfc3339(
                review.reviewed_at,
                path=f"{path} (id={review.id!r}).reviewed_at",
            )
            if not review.claim_refs:
                errors.append(f"{path} (id={review.id!r}).claim_refs: must not be empty")
            if len(set(review.claim_refs)) != len(review.claim_refs):
                errors.append(
                    f"{path} (id={review.id!r}).claim_refs: duplicate occurrence reference"
                )
            for ref in review.claim_refs:
                if CLAIM_REF_PATTERN.fullmatch(ref) is None:
                    errors.append(
                        f"{path} (id={review.id!r}).claim_refs: invalid occurrence "
                        f"reference {ref!r}; expected claim:<video_id>:<section>:<index>"
                    )
            if review.supersedes is not None and not review.supersedes.strip():
                errors.append(f"{path} (id={review.id!r}).supersedes: must be non-empty")
        for review in self.reviews:
            if review.supersedes is not None and review.supersedes not in by_id:
                errors.append(
                    f"{_location(review)} (id={review.id!r}).supersedes: "
                    f"unknown review ID {review.supersedes!r}"
                )
        errors.extend(_supersession_cycle_errors(self.reviews))
        if errors:
            raise _error("\n".join(sorted(set(errors))))

    def validate(self, corpus: NormalizedCorpus | None = None) -> ClaimLedger:
        """Validate structure, and optionally references and fingerprints."""

        self._validate_review_structure()
        if corpus is not None:
            self.apply(corpus)
        return self

    def apply(self, corpus: NormalizedCorpus) -> tuple[ClaimResolution, ...]:
        """Bind all reviews to claim occurrences in deterministic order."""

        if not isinstance(corpus, NormalizedCorpus):
            raise TypeError("corpus must be a NormalizedCorpus value")
        occurrences = _claim_occurrences(corpus)
        by_ref = {occurrence.ref: occurrence for occurrence in occurrences}
        errors: list[str] = []
        applications = self._applications()
        for review in self.reviews:
            missing = [ref for ref in review.claim_refs if ref not in by_ref]
            if missing:
                errors.append(
                    f"{review.location} (id={review.id!r}).claim_refs: "
                    f"dangling occurrence reference(s): {', '.join(missing)}"
                )
                continue
            targeted = [by_ref[ref] for ref in review.claim_refs]
            if not review.mixed_group:
                mismatches = [
                    occurrence.ref
                    for occurrence in targeted
                    if occurrence.claim_fingerprint != review.claim_fingerprint
                ]
                if mismatches:
                    # Include every target, not only the first mismatch.  This
                    # makes a bad grouping review auditable from one error.
                    errors.append(
                        f"{review.location} (id={review.id!r}).claim_fingerprint mismatch "
                        f"for targeted claim refs: {', '.join(review.claim_refs)}"
                    )
        errors.extend(self._conflict_errors(applications))
        if errors:
            raise _error("\n".join(sorted(set(errors))))

        resolutions: list[ClaimResolution] = []
        for occurrence in occurrences:
            targeted = tuple(
                applications[review.id]
                for review in self.reviews
                if occurrence.ref in review.claim_refs
            )
            resolutions.append(
                ClaimResolution(
                    occurrence=occurrence,
                    reviews=targeted,
                    status=_resolution_status(targeted),
                )
            )
        return tuple(resolutions)

    def resolve(self, corpus: NormalizedCorpus) -> tuple[ClaimResolution, ...]:
        """Compatibility alias for :meth:`apply`."""

        return self.apply(corpus)

    @classmethod
    def load(
        cls,
        source_root: str | Path | None = None,
        documents: Mapping[str, object] | None = None,
        *,
        overlay_root: str | Path | None = None,
        source: str | Path | None = None,
    ) -> ClaimLedger:
        """Load a claim ledger using the source-relative overlay contract."""

        return load_claim_ledger(
            source_root=source_root,
            documents=documents,
            overlay_root=overlay_root,
            source=source,
        )

    def _applications(self) -> dict[str, ReviewApplication]:
        superseded_by: dict[str, list[str]] = defaultdict(list)
        for review in self.reviews:
            if review.supersedes is not None:
                superseded_by[review.supersedes].append(review.id)
        policy_cutoff = self.policy.stale_after_datetime if self.policy else None
        result: dict[str, ReviewApplication] = {}
        for review in self.reviews:
            if superseded_by.get(review.id):
                status = "superseded"
            elif review.status == "stale":
                status = "stale"
            elif (
                policy_cutoff is not None
                and review.status in ACTIVE_REVIEW_STATUSES
                and _parse_rfc3339(review.reviewed_at, path=review.location) < policy_cutoff
            ):
                status = "stale"
            else:
                status = review.status
            result[review.id] = ReviewApplication(
                review=review,
                status=status,
                superseded_by=tuple(sorted(superseded_by.get(review.id, ()))),
            )
        return result

    def _conflict_errors(self, applications: Mapping[str, ReviewApplication]) -> list[str]:
        by_ref: dict[str, list[ReviewApplication]] = defaultdict(list)
        for review in self.reviews:
            application = applications[review.id]
            if not application.active:
                continue
            for ref in review.claim_refs:
                by_ref[ref].append(application)
        errors: list[str] = []
        for ref, active in sorted(by_ref.items()):
            if len(active) < 2:
                continue
            details = "; ".join(
                f"{application.review.id!r} at {application.review.location}"
                for application in sorted(active, key=lambda item: item.review.id)
            )
            errors.append(
                f"conflicting active reviews for occurrence {ref}: {details}; "
                "explicit supersession is required"
            )
        return errors


def _supersession_cycle_errors(reviews: Iterable[ClaimReview]) -> list[str]:
    by_id = {review.id: review for review in reviews}
    state: dict[str, int] = {}
    cycles: list[str] = []

    def visit(review_id: str, trail: list[str]) -> None:
        marker = state.get(review_id, 0)
        if marker == 1:
            start = trail.index(review_id)
            cycle = trail[start:] + [review_id]
            locations = "; ".join(
                f"{review_id!r} at {by_id[review_id].location}"
                for review_id in sorted(set(cycle[:-1]))
                if review_id in by_id
            )
            cycles.append("supersession cycle: " + " -> ".join(cycle) + f" ({locations})")
            return
        if marker == 2:
            return
        state[review_id] = 1
        review = by_id.get(review_id)
        if review is not None and review.supersedes is not None:
            visit(review.supersedes, [*trail, review.supersedes])
        state[review_id] = 2

    for review_id in sorted(by_id):
        visit(review_id, [review_id])
    return cycles


def _claim_occurrences(corpus: NormalizedCorpus) -> tuple[ClaimOccurrence, ...]:
    occurrences: list[ClaimOccurrence] = []
    for video in corpus.videos:
        for index, claim in enumerate(video.key_claims):
            ref = f"claim:{video.video_id}:{CLAIM_SECTION}:{index}"
            occurrences.append(
                ClaimOccurrence(
                    ref=ref,
                    video_id=video.video_id,
                    section=CLAIM_SECTION,
                    index=index,
                    claim=claim,
                )
            )
    return tuple(occurrences)


def _resolution_status(applications: tuple[ReviewApplication, ...]) -> str:
    if not applications:
        return UNREVIEWED_STATUS
    active = tuple(application for application in applications if application.active)
    if active:
        return active[0].status
    statuses = {application.status for application in applications}
    if "stale" in statuses:
        return "stale"
    return "superseded"


def load_claim_ledger(
    source_root: str | Path | None = None,
    documents: Mapping[str, object] | None = None,
    *,
    overlay_root: str | Path | None = None,
    source: str | Path | None = None,
) -> ClaimLedger:
    """Load the source-relative claim ledger and validate its structure."""

    if source_root is None and source is not None:
        source_root = source
    if documents is not None and "reviews" in documents and "claims" not in documents:
        documents = {"claims": documents}
    registries = load_registries(
        source_root=source_root,
        documents=documents,
        overlay_root=overlay_root,
    )
    return ClaimLedger.from_registries(registries)


load_ledger = load_claim_ledger
load_claim_reviews = load_claim_ledger
ClaimReviewLedger = ClaimLedger
ReviewLedger = ClaimLedger
ClaimLedgerError = ClaimLedgerValidationError
validate_claim_ledger = ClaimLedger.validate
apply_claim_ledger = ClaimLedger.apply
load_claim_review_ledger = load_claim_ledger
build_claim_ledger = load_claim_ledger
apply_claim_reviews = apply_claim_ledger
resolve_claims = apply_claim_ledger
validate_claim_reviews = ClaimLedger.validate


__all__ = [
    "ACTIVE_REVIEW_STATUSES",
    "CLAIM_FINGERPRINT_PATTERN",
    "CLAIM_SECTION",
    "ClaimLedger",
    "ClaimLedgerError",
    "ClaimLedgerValidationError",
    "ClaimOccurrence",
    "ClaimResolution",
    "ClaimReview",
    "ClaimReviewLedger",
    "ClaimReviewValidationError",
    "LedgerValidationError",
    "NON_ACTIVE_REVIEW_STATUSES",
    "REVIEW_STATUSES",
    "ReviewLedger",
    "ReviewApplication",
    "UNREVIEWED_STATUS",
    "apply_claim_ledger",
    "load_claim_ledger",
    "load_claim_review_ledger",
    "load_claim_reviews",
    "load_ledger",
    "build_claim_ledger",
    "apply_claim_reviews",
    "resolve_claims",
    "validate_claim_reviews",
    "validate_claim_ledger",
]
