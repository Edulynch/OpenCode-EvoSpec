from __future__ import annotations

import unittest
from pathlib import Path

from evospec.domain import OperationalState, ReviewResult, VerificationEvidence
from evospec.lifecycle import LifecycleState, Phase, ReviewVerdict
from evospec.services import CoreService
from evospec.workflow import WorkflowService
from tests._tempdir import TemporaryDirectory


class WorkflowRecoveryTests(unittest.TestCase):
    def test_status_and_continue_reload_persisted_review_and_depth_facts(self) -> None:
        # Given: persisted implementation work with workflow-computed requirements
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Foundation storage migration")
            workflow.record_progress("implement", "storage", (), "work", (), "resume")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            before = CoreService.open(root).recover().revision

            # When: fresh facades report recovery views
            status = WorkflowService.open(root).status()
            continued = WorkflowService.open(root).continue_work()

            # Then: both views expose persisted facts without mutating authority
            self.assertEqual(status.classification, "FOUNDATION")
            self.assertEqual(status.review_verdict, "BLOCKED")
            self.assertEqual(status.review_depth, "DEEP")
            self.assertEqual(continued.verification_sufficient, False)
            self.assertEqual(CoreService.open(root).recover().revision, before)

    def test_converge_pause_reloads_and_resumes_exact_pair(self) -> None:
        # Given: verified work at the persisted convergence boundary
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Export capability")
            workflow.record_progress("implement", "export", (), "work", (), "resume")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("unit tests",), True))
            core = CoreService.open(root)
            state = core.recover()
            core.advance_lifecycle(state, LifecycleState.VERIFYING, Phase.CONVERGE)
            paused = WorkflowService.open(root).pause()

            # When: a fresh facade resumes after disk reload
            resumed = WorkflowService.open(root).continue_work()

            # Then: the durable pair returns to CONVERGE exactly
            self.assertEqual(paused.phase, "CONVERGE")
            self.assertEqual(resumed.phase, "CONVERGE")
            self.assertEqual(resumed.code, "RESUMED")

    def test_side_request_becomes_pending_intake_without_automatic_execution(self) -> None:
        # Given: primary work, an active side change, and a further unrelated request
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()
            workflow.new("Primary capability")
            workflow.record_progress("implement", "primary", (), "work", (), "resume")
            side = workflow.change("unrelated: documentation")
            deferred = workflow.change("unrelated: later request")

            # When: the side work explicitly reaches a terminal outcome
            completed = workflow.reject()
            reloaded = WorkflowService.open(Path(temporary))

            # Then: Core restores primary and retains non-lifecycle intake until explicit use
            self.assertEqual(side.code, "SIDE_ACTIVE")
            self.assertEqual(deferred.code, "DEFERRED")
            self.assertEqual(completed.code, "PRIMARY_RESTORED")
            self.assertEqual(reloaded.status().pending_intake_id, deferred.pending_intake_id)
            self.assertEqual(reloaded.continue_work().code, "NOTHING_TO_RESUME")

    def test_unavailable_verification_blocks_convergence_and_pause_resumes_exact_pair(self) -> None:
        # Given: implementation with an acceptable review
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()
            workflow.new("Export capability")
            workflow.record_progress("implement", "export", (), "work", (), "resume")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))

            # When: verification is unavailable, then paused and recovered
            unavailable = workflow.record_verification(VerificationEvidence(("tool unavailable",), False))
            paused = workflow.pause()
            resumed = WorkflowService.open(Path(temporary)).continue_work()

            # Then: convergence remains guarded and the VERIFY pair returns exactly
            self.assertEqual(unavailable.code, "INSUFFICIENT")
            self.assertEqual(paused.code, "PAUSED")
            self.assertEqual(resumed.code, "RESUMED")
            self.assertEqual(resumed.phase, "VERIFY")

    def test_park_releases_the_active_slot_and_explicit_resume_restores_exact_verify_snapshot(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            unknown_resume = workflow.resume("change-unknown")
            workflow.new("Parkable capability")
            workflow.record_progress("implement", "parkable", ("implemented",), "verify", ("retry verification",), "resume verification")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            insufficient = workflow.record_verification(VerificationEvidence(("blocked runner",), False))
            snapshot = CoreService.open(root).recover().active_context
            if snapshot is None:
                self.fail("expected active verification snapshot")

            parked = workflow.park("waiting for verification runner")
            parked_state = CoreService.open(root).recover()
            same_feature = workflow.change("Parkable capability")
            independent = workflow.new("Independent capability")
            if snapshot.change_id is None:
                self.fail("expected parked change identity")
            occupied_resume = workflow.resume(str(snapshot.change_id))
            terminal = workflow.reject()
            restored = WorkflowService.open(root).resume(str(snapshot.change_id))
            restored_state = CoreService.open(root).recover()
            parked_again = workflow.park("awaiting a second verification attempt")
            duplicate_park = workflow.park("duplicate parking request")

            self.assertEqual(unknown_resume.code, "NO_PARKED_WORK")
            self.assertEqual(parked.code, "PARKED")
            self.assertIsNone(parked_state.active_context)
            self.assertEqual(len(parked_state.work_items), 1)
            item = parked_state.work_items[0]
            if item.context is None:
                self.fail("expected queued lifecycle context")
            self.assertEqual(item.context.autonomy_envelope, snapshot.autonomy_envelope)
            self.assertEqual(item.context.autonomy_metrics.operational_handoffs, snapshot.autonomy_metrics.operational_handoffs + 1)
            self.assertEqual(item.reason, "waiting for verification runner")
            self.assertTrue(item.transitioned_at)
            self.assertEqual(WorkflowService.open(root).continue_work().code, "NOTHING_TO_RESUME")
            self.assertEqual(same_feature.code, "PARKED_WORK_CONFLICT")
            self.assertEqual(independent.code, "PROPOSED")
            self.assertEqual(occupied_resume.code, "ACTIVE_WORK")
            self.assertEqual(terminal.code, "REJECTED")
            self.assertEqual(restored.code, "RESUMED")
            if restored_state.active_context is None:
                self.fail("expected resumed lifecycle context")
            self.assertEqual(restored_state.active_context.autonomy_envelope, snapshot.autonomy_envelope)
            self.assertEqual(restored_state.active_context.autonomy_metrics.operational_handoffs, snapshot.autonomy_metrics.operational_handoffs + 2)
            self.assertIsNone(restored_state.parked_work)
            self.assertEqual(parked_again.code, "PARKED")
            self.assertEqual(duplicate_park.code, "NO_ACTIVE_WORK")

    def test_legacy_resume_retains_frozen_recovery_compatibility(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Frozen recovery capability")
            workflow.park("deferred")
            item = CoreService.open(root).recover().work_items[0]
            CoreService.open(root).set_work_state(
                CoreService.open(root).recover(), item.id, OperationalState.FROZEN,
            )

            resumed = workflow.resume(str(item.id))

            self.assertEqual(resumed.code, "RESUMED")
            self.assertEqual(CoreService.open(root).recover().active_work_id, item.id)

    def test_paused_active_work_still_blocks_intake(self) -> None:
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()
            workflow.new("Paused capability")
            workflow.record_progress("implement", "paused", (), "work", (), "resume")
            workflow.pause()

            blocked = workflow.new("Paused capability")

            self.assertEqual(blocked.code, "ACTIVE_WORK")
            context = CoreService.open(Path(temporary)).recover().active_context
            if context is None:
                self.fail("expected paused active context")
            self.assertEqual(context.status, LifecycleState.PAUSED)

    def test_park_preserves_side_primary_deferred_and_pending_ownership_metadata(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Primary capability")
            workflow.record_progress("implement", "primary", (), "work", (), "resume")
            workflow.change("unrelated: side capability")
            workflow.change("unrelated: deferred capability")
            side_snapshot = CoreService.open(root).recover()
            parked_side = workflow.park("pause side work")
            side_parked_state = CoreService.open(root).recover()
            if side_snapshot.active_context is None or side_snapshot.active_context.change_id is None:
                self.fail("expected parked side change identity")
            workflow.resume(str(side_snapshot.active_context.change_id))
            workflow.reject()
            pending_snapshot = CoreService.open(root).recover()
            parked_pending = workflow.park("pause pending intake")
            pending_parked_state = CoreService.open(root).recover()

            self.assertEqual(parked_side.code, "PARKED")
            if not side_parked_state.work_items:
                self.fail("expected queued side snapshot")
            queued_side = side_parked_state.work_items[0]
            if queued_side.context is None:
                self.fail("expected queued side lifecycle context")
            self.assertEqual(queued_side.context.autonomy_envelope, side_snapshot.active_context.autonomy_envelope)
            self.assertEqual(queued_side.context.autonomy_metrics.operational_handoffs, side_snapshot.active_context.autonomy_metrics.operational_handoffs + 1)
            self.assertEqual(queued_side.preserved_primary_context, side_snapshot.preserved_primary_context)
            self.assertEqual(queued_side.deferred_side_request, side_snapshot.deferred_side_request)
            self.assertEqual(parked_pending.code, "PARKED")
            if not pending_parked_state.work_items:
                self.fail("expected queued pending snapshot")
            queued_pending = pending_parked_state.work_items[0]
            if queued_pending.context is None or pending_snapshot.active_context is None:
                self.fail("expected queued pending lifecycle context")
            self.assertEqual(queued_pending.context.autonomy_envelope, pending_snapshot.active_context.autonomy_envelope)
            self.assertEqual(queued_pending.context.autonomy_metrics.operational_handoffs, pending_snapshot.active_context.autonomy_metrics.operational_handoffs + 1)
            self.assertEqual(queued_pending.pending_intake, pending_snapshot.pending_intake)
