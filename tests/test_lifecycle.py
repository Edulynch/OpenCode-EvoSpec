from __future__ import annotations

import unittest

from evospec.domain import ReviewResult, VerificationEvidence
from evospec.lifecycle import (
    LifecycleError,
    LifecycleState,
    Phase,
    ReviewVerdict,
    advance,
    validate_context_state,
    is_valid_pair,
)


class LifecycleTests(unittest.TestCase):
    def test_valid_pairs_and_terminal_progression(self) -> None:
        # Given: caller supplied checkpoints for convergence
        review = ReviewResult(ReviewVerdict.PASS, (), True)
        evidence = VerificationEvidence(("tests",), True)

        # When: the caller advances through valid transitions
        status, phase = advance(LifecycleState.PROPOSED, Phase.RESEARCH, LifecycleState.READY, Phase.READY, False, review, evidence)
        status, phase = advance(status, phase, LifecycleState.IN_PROGRESS, Phase.IMPLEMENT, False, review, evidence)
        status, phase = advance(status, phase, LifecycleState.IN_PROGRESS, Phase.REVIEW, False, review, evidence)
        status, phase = advance(status, phase, LifecycleState.VERIFYING, Phase.VERIFY, False, review, evidence)
        status, phase = advance(status, phase, LifecycleState.VERIFYING, Phase.CONVERGE, False, review, evidence)
        status, phase = advance(status, phase, LifecycleState.ACCEPTED, Phase.DONE, False, review, evidence, True)

        # Then: only caller-recorded convergence reaches done
        self.assertEqual((status, phase), (LifecycleState.ACCEPTED, Phase.DONE))

    def test_pause_retains_exact_pair_and_terminals_cannot_resume(self) -> None:
        # Given: a paused review pair and a terminal change
        review = ReviewResult(ReviewVerdict.PASS, (), True)
        evidence = VerificationEvidence(("tests",), True)

        # When/Then: resumption is exact and terminals are final
        self.assertEqual(
            advance(LifecycleState.PAUSED, Phase.REVIEW, LifecycleState.IN_PROGRESS, Phase.REVIEW, False, review, evidence),
            (LifecycleState.IN_PROGRESS, Phase.REVIEW),
        )
        with self.assertRaises(LifecycleError):
            advance(LifecycleState.REJECTED, Phase.CLARIFY, LifecycleState.READY, Phase.READY, False, review, evidence)

    def test_pause_and_resume_preserve_verify_and_converge_pairs(self) -> None:
        # Given: persisted verification and convergence work
        review = ReviewResult(ReviewVerdict.PASS, (), True)
        evidence = VerificationEvidence(("tests",), True)

        # When: each state is explicitly paused then resumed
        for phase in (Phase.VERIFY, Phase.CONVERGE):
            with self.subTest(phase=phase):
                paused = advance(
                    LifecycleState.VERIFYING,
                    phase,
                    LifecycleState.PAUSED,
                    phase,
                    False,
                    review,
                    evidence,
                )
                resumed = advance(
                    *paused,
                    LifecycleState.VERIFYING,
                    phase,
                    False,
                    review,
                    evidence,
                )

                # Then: the original state-phase pair is restored exactly
                self.assertEqual(resumed, (LifecycleState.VERIFYING, phase))

    def test_invalid_pairs_guards_and_repair_limit_are_rejected(self) -> None:
        # Given: incomplete caller data
        review = ReviewResult(ReviewVerdict.BLOCKED, (), False)
        evidence = VerificationEvidence((), False)

        # When/Then: lifecycle validation preserves caller control
        with self.assertRaises(LifecycleError):
            validate_context_state(LifecycleState.READY, Phase.READY, True, None, evidence, 0, None, None)
        with self.assertRaises(LifecycleError):
            advance(LifecycleState.VERIFYING, Phase.VERIFY, LifecycleState.ACCEPTED, Phase.DONE, False, review, evidence)
        with self.assertRaises(LifecycleError):
            validate_context_state(LifecycleState.IN_PROGRESS, Phase.IMPLEMENT, False, review, evidence, 3, None, None)
        with self.assertRaises(LifecycleError):
            validate_context_state(LifecycleState.PAUSED, Phase.IMPLEMENT, False, review, evidence, 2, (LifecycleState.IN_PROGRESS, Phase.IMPLEMENT), "blocked", None)

    def test_all_mapped_pairs_are_valid_and_cross_pairs_are_not(self) -> None:
        # Given: every explicit persisted phase mapping
        valid = {
            (LifecycleState.PROPOSED, Phase.RESEARCH),
            (LifecycleState.CLARIFYING, Phase.CLARIFY),
            (LifecycleState.READY, Phase.READY),
            (LifecycleState.IN_PROGRESS, Phase.IMPLEMENT),
            (LifecycleState.IN_PROGRESS, Phase.REVIEW),
            (LifecycleState.VERIFYING, Phase.VERIFY),
            (LifecycleState.VERIFYING, Phase.CONVERGE),
            (LifecycleState.ACCEPTED, Phase.DONE),
        }

        # When: every non-paused, non-terminal combination is examined
        observed = {
            (status, phase)
            for status in (LifecycleState.PROPOSED, LifecycleState.CLARIFYING, LifecycleState.READY, LifecycleState.IN_PROGRESS, LifecycleState.VERIFYING, LifecycleState.ACCEPTED)
            for phase in Phase
            if is_valid_pair(status, phase)
        }

        # Then: no unplanned status/phase combination is admitted
        self.assertEqual(observed, valid)

    def test_verify_requires_recorded_review_and_done_requires_completed_convergence(self) -> None:
        # Given: caller attempts to skip review or convergence guards
        passing_review = ReviewResult(ReviewVerdict.PASS, (), True)
        sufficient_evidence = VerificationEvidence(("tests",), True)

        # When/Then: the state machine rejects each skipped gate
        with self.assertRaises(LifecycleError):
            advance(LifecycleState.IN_PROGRESS, Phase.IMPLEMENT, LifecycleState.VERIFYING, Phase.VERIFY, False, passing_review, sufficient_evidence, False)
        with self.assertRaises(LifecycleError):
            advance(LifecycleState.IN_PROGRESS, Phase.REVIEW, LifecycleState.VERIFYING, Phase.VERIFY, False, None, sufficient_evidence, False)
        with self.assertRaises(LifecycleError):
            advance(LifecycleState.VERIFYING, Phase.CONVERGE, LifecycleState.ACCEPTED, Phase.DONE, False, passing_review, sufficient_evidence, False)
        self.assertEqual(
            advance(LifecycleState.VERIFYING, Phase.CONVERGE, LifecycleState.ACCEPTED, Phase.DONE, False, passing_review, sufficient_evidence, True),
            (LifecycleState.ACCEPTED, Phase.DONE),
        )

    def test_terminal_outcomes_retain_their_prior_valid_phase(self) -> None:
        # Given: a clarifying change that is explicitly rejected
        review = ReviewResult(ReviewVerdict.PASS, (), True)
        evidence = VerificationEvidence(("tests",), True)

        # When: caller records terminal status
        status, phase = advance(LifecycleState.CLARIFYING, Phase.CLARIFY, LifecycleState.REJECTED, Phase.CLARIFY, False, review, evidence, False)

        # Then: traceability retains rather than invents its phase
        self.assertEqual((status, phase), (LifecycleState.REJECTED, Phase.CLARIFY))
        with self.assertRaises(LifecycleError):
            advance(LifecycleState.CLARIFYING, Phase.CLARIFY, LifecycleState.REJECTED, Phase.IMPLEMENT, False, review, evidence, False)

    def test_exact_phase_edges_reject_skips_and_unnamed_backtracking(self) -> None:
        # Given: sufficient facts that would otherwise satisfy later lifecycle guards
        review = ReviewResult(ReviewVerdict.PASS, (), True)
        evidence = VerificationEvidence(("tests",), True)

        # When/Then: state-level reachability cannot bypass an exact phase edge
        forbidden = (
            (LifecycleState.READY, Phase.READY, LifecycleState.IN_PROGRESS, Phase.REVIEW),
            (LifecycleState.IN_PROGRESS, Phase.IMPLEMENT, LifecycleState.VERIFYING, Phase.VERIFY),
            (LifecycleState.IN_PROGRESS, Phase.REVIEW, LifecycleState.IN_PROGRESS, Phase.IMPLEMENT),
            (LifecycleState.VERIFYING, Phase.VERIFY, LifecycleState.IN_PROGRESS, Phase.REVIEW),
        )
        for current_status, current_phase, requested_status, requested_phase in forbidden:
            with self.subTest(edge=(current_status, current_phase, requested_status, requested_phase)):
                with self.assertRaises(LifecycleError):
                    advance(
                        current_status,
                        current_phase,
                        requested_status,
                        requested_phase,
                        False,
                        review,
                        evidence,
                    )
