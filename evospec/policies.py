"""Pure deterministic workflow policies with no persistence authority."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import assert_never

from .domain import (
    ReviewEvidence,
    ReviewEvidenceCategory,
    ReviewFinding,
    ReviewResult,
    SafetyFacts,
    State,
)
from .lifecycle import Classification, ReviewVerdict


class IntakeOperation(StrEnum):
    """The authorized workflow operation that supplies intake defaults."""

    NEW = "NEW"
    CHANGE = "CHANGE"


@dataclass(frozen=True, slots=True)
class CapabilityRoute:
    """Pure, inspectable established-capability resolution result."""

    code: str
    feature_id: str | None = None
    provenance: tuple[str, ...] = ()


_LEXICAL_WORDS = re.compile(r"[a-z][a-z0-9-]{2,}")
_GENERIC_CAPABILITY_TERMS = frozenset(
    {
        "add",
        "an",
        "at",
        "behavior",
        "by",
        "capability",
        "change",
        "create",
        "disable",
        "enable",
        "experiment",
        "feature",
        "fix",
        "for",
        "foundation",
        "from",
        "implement",
        "initial",
        "into",
        "make",
        "migration",
        "modify",
        "of",
        "on",
        "patch",
        "rejected",
        "remove",
        "support",
        "the",
        "tune",
        "update",
        "use",
        "uses",
        "with",
    }
)


class IntakePolicy:
    """Classify intent internally from deterministic request markers."""

    def classify(self, operation: IntakeOperation, intent: str) -> Classification:
        """Choose a classification without exposing classification selection to callers."""
        action = intent.strip().casefold().split(maxsplit=1)[0].rstrip(":")
        if action == "experiment":
            return Classification.EXPERIMENT
        if action in ("foundation", "migrate", "migration"):
            return Classification.FOUNDATION
        if action in ("tune", "adjust"):
            return Classification.TUNE
        if action in ("fix", "patch", "change"):
            return Classification.PATCH
        match operation:
            case IntakeOperation.NEW:
                return Classification.FEATURE
            case IntakeOperation.CHANGE:
                return Classification.PATCH
            case unreachable:
                assert_never(unreachable)

    def is_unrelated(self, intent: str) -> bool:
        """Recognize the explicit deterministic side-work marker used by this MVP."""
        return intent.casefold().startswith("unrelated:")

    def linked_capabilities(self, intent: str) -> tuple[str, ...]:
        """Split the explicit independent-capability marker without inferring semantics."""
        capabilities = tuple(item.strip() for item in intent.split(" | ") if item.strip())
        return capabilities if len(capabilities) > 1 else ()

    def research(
        self,
        state: State,
        classification: Classification,
        provenance: tuple[str, ...] = (),
    ) -> SafetyFacts:
        """Convert deterministic discovery into proportional persisted requirements."""
        match classification:
            case Classification.TUNE:
                depths = ("TARGETED", "TARGETED", "TARGETED")
            case Classification.PATCH:
                depths = ("LIGHT", "TARGETED", "LIGHT")
            case Classification.FEATURE | Classification.EXPERIMENT:
                depths = ("NORMAL", "NORMAL", "NORMAL")
            case Classification.FOUNDATION:
                depths = ("DEEP", "FULL", "FULL")
        initialization_scope = (
            ()
            if state.initialization is None
            else tuple(str(item.path) for item in state.initialization.evidence)
        )
        scope = tuple(
            dict.fromkeys(
                initialization_scope
                + tuple(str(item.path) for item in state.publications)
                + tuple(f"decision:{item.id}" for item in state.decisions)
                + provenance
            )
        )
        risks = () if classification == Classification.TUNE else (classification.value.lower(),)
        return SafetyFacts((), scope, risks, *depths)

    def resolve_capability(self, state: State, intent: str, project_candidates: tuple[tuple[str, tuple[str, ...]], ...]) -> CapabilityRoute:
        """Resolve a unique existing capability with Core authority before native evidence."""
        terms = self._terms(intent)
        active = state.active_context
        if active is not None:
            feature = next((item for item in state.features if item.id == active.feature_id), None)
            if feature is not None and self._same_capability(feature.title, intent, terms):
                return CapabilityRoute("CORE_CAPABILITY", str(feature.id), self._core_provenance(state, feature.id))
        features = tuple(item for item in state.features if self._same_capability(item.title, intent, terms))
        if len(features) == 1:
            return CapabilityRoute("CORE_CAPABILITY", str(features[0].id), self._core_provenance(state, features[0].id))
        if len(features) > 1:
            return CapabilityRoute("AMBIGUOUS_CAPABILITY")
        published = tuple(
            item
            for item in state.publications
            if item.feature_id is not None and self._same_capability(item.capability, intent, terms)
        )
        if len(published) == 1:
            publication = published[0]
            if publication.feature_id is not None:
                return CapabilityRoute("CORE_CAPABILITY", str(publication.feature_id), self._core_provenance(state, publication.feature_id))
        if len(published) > 1:
            return CapabilityRoute("AMBIGUOUS_CAPABILITY")
        matching_candidates = tuple(
            candidate for candidate in project_candidates if self._matches(candidate[0], terms)
        )
        if len(matching_candidates) == 1:
            name, provenance = matching_candidates[0]
            return CapabilityRoute("PROJECT_CAPABILITY", None, (f"candidate:{name}",) + provenance)
        if len(matching_candidates) > 1:
            return CapabilityRoute("AMBIGUOUS_CAPABILITY")
        return CapabilityRoute("NO_ESTABLISHED_CAPABILITY")

    def _terms(self, text: str) -> frozenset[str]:
        """Keep only stable capability words for literal overlap matching."""
        return frozenset(
            word
            for word in _LEXICAL_WORDS.findall(text.casefold())
            if word not in _GENERIC_CAPABILITY_TERMS
        )

    def _matches(self, text: str, terms: frozenset[str]) -> bool:
        """Require two literal meaningful terms without semantic inference."""
        text_terms = self._terms(text)
        return len(terms) >= 2 and len(text_terms) >= 2 and len(text_terms & terms) >= 2

    def _same_capability(self, text: str, intent: str, terms: frozenset[str]) -> bool:
        """Match an established capability only when its complete name is requested."""
        established_terms = self._terms(text)
        return bool(established_terms) and established_terms <= terms

    def _core_provenance(self, state: State, feature_id: str) -> tuple[str, ...]:
        """Return canonical Core references without promoting unaccepted evidence."""
        publications = tuple(
            str(item.path) for item in state.publications if str(item.feature_id) == feature_id
        )
        return (f"feature:{feature_id}",) + publications


@dataclass(frozen=True, slots=True)
class AssurancePolicy:
    """Evaluate supplied review severities with explicit precedence."""

    def evaluate(self, severities: tuple[str, ...], complete: bool) -> ReviewResult:
        """Return BLOCKED over WARN over PASS and never pass incomplete inputs."""
        normalized = tuple(item.upper() for item in severities)
        if not complete or "BLOCKED" in normalized:
            return ReviewResult(ReviewVerdict.BLOCKED, normalized, False)
        if "WARN" in normalized:
            return ReviewResult(ReviewVerdict.WARN, normalized, False)
        return ReviewResult(ReviewVerdict.PASS, normalized, True)

    def evaluate_records(
        self,
        evidence: tuple[ReviewEvidence, ...],
        findings: tuple[ReviewFinding, ...],
    ) -> ReviewResult:
        """Derive a review result from the persisted structured review inputs."""
        required = frozenset(
            (
                ReviewEvidenceCategory.WORKFLOW,
                ReviewEvidenceCategory.REQUIREMENTS,
                ReviewEvidenceCategory.CLARIFICATION,
                ReviewEvidenceCategory.CONSTRAINT,
                ReviewEvidenceCategory.PRODUCT_TRUTH,
                ReviewEvidenceCategory.PROGRESS,
            )
        )
        complete = required.issubset({item.category for item in evidence}) and all(
            item.value != "MISSING" for item in evidence if item.required
        )
        return self.evaluate(
            tuple(item.severity for item in findings),
            complete,
        )
