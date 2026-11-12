from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from evospec.domain import (
    Change,
    ChangeId,
    Feature,
    FeatureId,
    ExpectedRevision,
    LivingSpecId,
    ReviewResult,
    VerificationEvidence,
)
from evospec.lifecycle import LifecycleState, Phase, ReviewVerdict
from evospec.persistence import BinaryFile, FileOperations, StateStore
from evospec.services import CoreService, ServiceError
from evospec.workflow import WorkflowService
from tests._tempdir import TemporaryDirectory
from tests.test_services_state import (
    advance_context,
    convergence_assessment,
    implementation_context,
)


class FailingReplace(FileOperations):
    def replace(self, source: Path, target: Path) -> None:
        raise OSError("injected replace failure")


class FailingFlush(FileOperations):
    def flush(self, handle: BinaryFile) -> None:
        raise OSError("injected flush failure")


class FailingWrite(FileOperations):
    def write(self, handle: BinaryFile, data: bytes) -> int:
        raise OSError("injected write failure")


class FailingFileFsync(FileOperations):
    def fsync_file(self, descriptor: int) -> None:
        raise OSError("injected fsync failure")


class FailingClose(FileOperations):
    def close(self, handle: BinaryFile) -> None:
        raise OSError("injected close failure")


class PartialWrite(FileOperations):
    def write(self, handle: BinaryFile, data: bytes) -> int:
        return len(data) - 1


class FailSecondReplace(FileOperations):
    def __init__(self) -> None:
        self.replace_count = 0

    def replace(self, source: Path, target: Path) -> None:
        self.replace_count += 1
        if self.replace_count == 2:
            raise OSError("injected state replacement failure")
        super().replace(source, target)


class FailingReplaceAndCleanup(FailingReplace):
    def unlink(self, path: Path) -> None:
        raise OSError("injected cleanup failure")


class RecordingOperations(FileOperations):
    def __init__(self) -> None:
        self.events: list[str] = []
        self.replace_parents: tuple[Path, Path] | None = None

    def write(self, handle: BinaryFile, data: bytes) -> int:
        self.events.append("write")
        return super().write(handle, data)

    def flush(self, handle: BinaryFile) -> None:
        self.events.append("flush")
        super().flush(handle)

    def fsync_file(self, descriptor: int) -> None:
        self.events.append("file_fsync")
        super().fsync_file(descriptor)

    def close(self, handle: BinaryFile) -> None:
        self.events.append("close")
        super().close(handle)

    def replace(self, source: Path, target: Path) -> None:
        self.events.append("replace")
        self.replace_parents = (source.parent, target.parent)
        super().replace(source, target)

    def fsync_directory(self, directory: Path) -> None:
        self.events.append("directory_fsync")


class FailingDirectoryFsync(FileOperations):
    def fsync_directory(self, directory: Path) -> None:
        raise OSError("injected directory fsync failure")


class AtomicPersistenceTests(unittest.TestCase):
    def test_legacy_recovery_state_publication_failure_keeps_original_authority(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Legacy recovery atomicity")
            stable_service = CoreService.open(root)
            state = stable_service.recover()
            active = state.active_context
            initialization = state.initialization
            assert active is not None and active.change_id is not None and initialization is not None
            state = stable_service.record_safety_facts(
                state,
                True,
                replace(active.safety_facts, unresolved_decision_details=("operational note",)),
            )
            before = StateStore(root).load_state()
            failing_service = CoreService.open(root, FailSecondReplace())

            with self.assertRaises(ServiceError) as raised:
                failing_service.recover_legacy_non_material_detail(
                    ExpectedRevision(before.revision),
                    initialization.repository_id,
                    str(active.feature_id),
                    str(active.change_id),
                    "operational note",
                )

            self.assertEqual(raised.exception.__cause__.__class__.__name__, "AtomicFailure")
            self.assertEqual(StateStore(root).load_state(), before)

    def test_atomic_publication_orders_events_and_uses_same_directory(self) -> None:
        with TemporaryDirectory() as temporary:
            operations = RecordingOperations()
            service = CoreService.open(Path(temporary), operations)

            service.record_feature(
                service.recover(), Feature(FeatureId("feature"), "Feature", None)
            )

            self.assertEqual(
                operations.events,
                ["write", "flush", "file_fsync", "close", "replace", "directory_fsync"],
            )
            self.assertIsNotNone(operations.replace_parents)
            if operations.replace_parents is not None:
                self.assertEqual(*operations.replace_parents)

    def test_directory_fsync_failure_does_not_reclassify_commit(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root, FailingDirectoryFsync())

            saved = service.record_feature(
                service.recover(), Feature(FeatureId("feature"), "Feature", None)
            )

            self.assertEqual(StateStore(root).load_state(), saved)

    def test_cleanup_failure_does_not_mask_atomic_failure(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root, FailingReplaceAndCleanup())

            with self.assertRaises(ServiceError) as raised:
                service.record_feature(
                    service.recover(), Feature(FeatureId("feature"), "Feature", None)
                )

            self.assertEqual(raised.exception.__cause__.__class__.__name__, "AtomicFailure")
            self.assertEqual(StateStore(root).load_state().revision, 0)
    def test_pre_replacement_failure_preserves_prior_authority(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            stable_service = CoreService.open(root)
            stable = stable_service.record_feature(
                stable_service.recover(), Feature(FeatureId("stable"), "Stable", None)
            )
            failing = CoreService.open(root, FailingReplace())

            with self.assertRaises(ServiceError):
                failing.record_feature(stable, Feature(FeatureId("next"), "Next", None))

            self.assertEqual(StateStore(root).load_state(), stable)
            self.assertEqual(list((root / ".evospec").glob("*.tmp")), [])

    def test_stale_revision_is_rejected_at_service_boundary(self) -> None:
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            stale = service.recover()
            service.record_feature(stale, Feature(FeatureId("first"), "First", None))

            with self.assertRaises(ServiceError):
                service.record_feature(stale, Feature(FeatureId("second"), "Second", None))

    def test_pre_replacement_io_failures_do_not_publish(self) -> None:
        for operations in (
            FailingWrite(),
            PartialWrite(),
            FailingFlush(),
            FailingFileFsync(),
            FailingClose(),
        ):
            with self.subTest(operations=operations.__class__.__name__), TemporaryDirectory() as temporary:
                root = Path(temporary)
                service = CoreService.open(root, operations)

                with self.assertRaises(ServiceError):
                    service.record_feature(
                        service.recover(), Feature(FeatureId("feature"), "Feature", None)
                    )

                self.assertEqual(StateStore(root).load_state().revision, 0)

    def test_record_written_before_failed_state_commit_remains_non_authoritative(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            stable_service = CoreService.open(root)
            feature = Feature(FeatureId("feature"), "Feature", None)
            state = stable_service.record_feature(stable_service.recover(), feature)
            failing_service = CoreService.open(root, FailSecondReplace())
            change = Change(
                ChangeId("orphan-change"), feature.id, "Orphaned before authority"
            )

            with self.assertRaises(ServiceError):
                failing_service.record_change(state, change)

            recovered = CoreService.open(root).recover()
            self.assertEqual(recovered, state)
            self.assertEqual(recovered.changes, ())
            self.assertTrue(
                (root / ".evospec" / "records" / "changes" / "orphan-change.json").is_file()
            )

            retried = stable_service.record_change(state, change)
            self.assertEqual(retried.changes[0].id, ChangeId("orphan-change"))

    def test_living_spec_written_before_failed_state_commit_is_non_authoritative(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root)
            feature = Feature(
                FeatureId("feature-main"), "Main", LivingSpecId("spec-main")
            )
            change = Change(ChangeId("change-main"), feature.id, "Publish truth")
            state = service.record_feature(service.recover(), feature)
            state = service.record_change(state, change)
            converged = replace(
                implementation_context(feature.id, change.id),
                status=LifecycleState.VERIFYING,
                phase=Phase.CONVERGE,
                review=ReviewResult(ReviewVerdict.PASS, (), True),
                verification=VerificationEvidence(("tests",), True),
                convergence_complete=True,
            )
            state = advance_context(service, state, converged)
            failing = CoreService.open(root, FailSecondReplace())

            with self.assertRaises(ServiceError):
                failing.publish_accepted_convergence(
                    state, feature.id, change.id, LivingSpecId("spec-main"), "1", "truth\n", convergence_assessment(change.id, True)
                )

            self.assertEqual(CoreService.open(root).recover(), state)
            self.assertTrue(
                (root / ".evospec" / "living-specs" / "spec-main" / "1.md").is_file()
            )
