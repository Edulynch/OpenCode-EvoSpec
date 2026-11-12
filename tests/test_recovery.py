from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from evospec.domain import (
    Change,
    ChangeId,
    Feature,
    FeatureId,
    ReviewResult,
    State,
    VerificationEvidence,
)
from evospec.lifecycle import LifecycleState, Phase, ReviewVerdict
from evospec.persistence import StateStore
from evospec.services import CoreService
from tests._tempdir import TemporaryDirectory
from tests.test_services_state import (
    advance_context,
    implementation_context,
    proposed_context,
)


class RecoveryTests(unittest.TestCase):
    def test_relative_repository_root_resolves_against_working_directory(self) -> None:
        working_directory = Path.cwd().resolve()
        with TemporaryDirectory(dir=working_directory) as temporary:
            absolute_root = Path(temporary).resolve()
            relative_root = absolute_root.relative_to(working_directory)
            service = CoreService.open(relative_root)

            saved = service.record_feature(
                service.recover(),
                Feature(FeatureId("feature-relative"), "Relative", None),
            )

            self.assertEqual(StateStore(relative_root).load_state(), saved)
            self.assertTrue((absolute_root / ".evospec" / "state.json").is_file())

    def test_absolute_native_repository_root_is_preserved(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            service = CoreService.open(root)

            saved = service.record_feature(
                service.recover(),
                Feature(FeatureId("feature-absolute"), "Absolute", None),
            )

            self.assertEqual(StateStore(root).load_state(), saved)
            self.assertTrue(root.is_absolute())

    def test_restart_recovers_active_and_preserved_context_without_conversation(self) -> None:
        # Given: a persisted side-work interruption
        with TemporaryDirectory() as temporary:
            first_caller = CoreService.open(Path(temporary))
            primary = implementation_context("feature-primary", "change-primary")
            side_context = implementation_context("feature-side", "change-side")
            side_context = replace(side_context, side_of=primary.feature_id)
            persisted = first_caller.record_feature(first_caller.recover(), Feature(primary.feature_id, "Primary", None))
            primary_change = Change(ChangeId("change-primary"), primary.feature_id, "Primary change")
            persisted = first_caller.record_change(persisted, primary_change)
            persisted = advance_context(first_caller, persisted, primary)
            persisted = first_caller.record_feature(persisted, Feature(side_context.feature_id, "Side", None, primary.feature_id))
            side_change = Change(ChangeId("change-side"), side_context.feature_id, "Side change")
            side = first_caller.activate_side(persisted, side_change)
            side = advance_context(first_caller, side, side_context)

            # When: a fresh caller recovers only from state.json
            recovered = CoreService.open(Path(temporary)).recover()

            # Then: both canonical contexts retain exact lifecycle, work, and resume facts
            self.assertEqual(recovered, side)
            self.assertEqual(recovered.active_context.current_action if recovered.active_context else None, "write")
            self.assertEqual(recovered.preserved_primary_context, primary)

    def test_missing_state_means_explicit_no_active_work(self) -> None:
        # Given/When: a repository never established state
        with TemporaryDirectory() as temporary:
            recovered = StateStore(Path(temporary)).load_state()

            # Then: recovery invents neither focus nor progress
            self.assertEqual(recovered, State(0, None))

    def test_restart_recovers_each_unfinished_phase_fact(self) -> None:
        # Given: caller-recorded interruption facts for each unfinished phase
        phases = (
            (LifecycleState.IN_PROGRESS, Phase.IMPLEMENT),
            (LifecycleState.IN_PROGRESS, Phase.REVIEW),
            (LifecycleState.VERIFYING, Phase.VERIFY),
            (LifecycleState.VERIFYING, Phase.CONVERGE),
        )
        for status, phase in phases:
            with self.subTest(phase=phase), TemporaryDirectory() as temporary:
                context = implementation_context("feature", "change")
                review = ReviewResult(ReviewVerdict.PASS, (), True)
                evidence = VerificationEvidence(("tests",), True)
                persisted_context = replace(context, status=status, phase=phase, review=review if phase != Phase.IMPLEMENT else None, verification=evidence if phase in (Phase.VERIFY, Phase.CONVERGE) else context.verification)
                service = CoreService.open(Path(temporary))
                state = service.record_feature(service.recover(), Feature(context.feature_id, "Feature", None))
                change = Change(ChangeId("change"), context.feature_id, "Change")
                state = service.record_change(state, change)

                # When: a caller persists then a fresh process recovers it
                saved = advance_context(service, state, persisted_context)

                # Then: phase, review, evidence, progress, and resume point survive intact
                self.assertEqual(CoreService.open(Path(temporary)).recover(), saved)
