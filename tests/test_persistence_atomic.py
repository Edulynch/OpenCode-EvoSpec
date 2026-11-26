from __future__ import annotations

import unittest
import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import cast

from evospec.domain import (
    Change,
    ChangeId,
    ContinuityError,
    ContinuityStateData,
    Feature,
    FeatureId,
    ExpectedRevision,
    LivingSpecId,
    ProvisionalSliceCriterion,
    ReviewResult,
    VerificationEvidence,
)
from evospec.lifecycle import LifecycleState, Phase, ReviewVerdict
from evospec.persistence import (
    AtomicFailure,
    BinaryFile,
    ContinuityPersistence,
    FileOperations,
    StateStore,
    RecoveryError,
    JsonValue,
    _provisional_slice_to_json,
    _new_d4_provisional_slice_revision,
    encode_json,
)
from evospec.continuity import execute as execute_continuity
from evospec.services import CoreService, ServiceError
from evospec.workflow import WorkflowError, WorkflowService
from tests._tempdir import TemporaryDirectory
from tests.test_services_state import (
    advance_context,
    convergence_assessment,
    implementation_context,
)
from tests.test_persistence import (
    _ProvisionalSliceTestHarness,
    _create_d4_session_request_fixture,
    _create_provisional_fixture,
    _make_provisional_revision,
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
    @staticmethod
    def _seed_continuity(
        root: Path,
    ) -> tuple[ContinuityPersistence, ContinuityStateData]:
        _ = execute_continuity(root, "specify", 0, "ses_size_bound_123", {
            "title": "Bounded journal",
            "outcome": "A pending journal remains recoverable.",
            "criteria": [{
                "description": "Persistence tests pass.",
                "check_kind": "python-unittest",
                "check_target": "tests.test_persistence_atomic",
            }],
            "increments": [],
            "tasks": [{"title": "Check recovery", "criteria": [0]}],
        })
        store = ContinuityPersistence(root)
        current = store.load()
        if current is None:
            raise AssertionError("expected initialized continuity state")
        return store, current

    def test_oversized_continuity_journal_is_rejected_before_publication(self) -> None:
        with TemporaryDirectory(prefix="evospec-continuity-size-") as temporary:
            root = Path(temporary)
            store, current = self._seed_continuity(root)
            candidate = deepcopy(current)
            candidate["revision"] += 1
            content = b"\x01" * 350_000
            writes: dict[str, tuple[bytes, str | None]] = {
                relative: (content, None)
                for relative in (
                    "specs/999-size-bound/spec.md",
                    "specs/999-size-bound/plan.md",
                )
            }
            marker = root / ".evospec" / "continuity.json"
            before_marker = marker.read_bytes()
            failing_store = ContinuityPersistence(root, FailSecondReplace())

            with self.assertRaises(ContinuityError) as raised:
                with failing_store.transaction():
                    failing_store.commit(current, candidate, writes)

            try:
                recovered = store.load()
            except ContinuityError as error:
                load_error = error.code
            else:
                load_error = None
                if (
                    recovered is None
                    or recovered["pending"] is not None
                    or recovered["revision"] != current["revision"]
                ):
                    load_error = "STATE_CHANGED"
            self.assertEqual(
                (
                    raised.exception.code,
                    marker.stat().st_size > 4 * 1024 * 1024,
                    load_error,
                    marker.read_bytes() == before_marker,
                    any((root / relative).exists() for relative in writes),
                ),
                ("LIMIT_EXCEEDED", False, None, True, False),
            )

    def test_continuity_state_reader_accepts_exact_bound_and_rejects_one_byte_over(self) -> None:
        with TemporaryDirectory(prefix="evospec-continuity-reader-bound-") as temporary:
            root = Path(temporary)
            store, current = self._seed_continuity(root)
            maximum = 4 * 1024 * 1024
            exact = deepcopy(current)
            documents = dict(exact["documents"])
            initial_path = "specs/999-" + "a" + "/spec.md"
            documents[initial_path] = "0" * 64
            exact["documents"] = documents
            padding = maximum - len(encode_json(cast(JsonValue, cast(object, exact))))
            self.assertGreater(padding, 0)
            _ = documents.pop(initial_path)
            exact_path = "specs/999-" + "a" * (padding + 1) + "/spec.md"
            documents[exact_path] = "0" * 64
            exact_bytes = encode_json(cast(JsonValue, cast(object, exact)))
            self.assertEqual(len(exact_bytes), maximum)

            marker = root / ".evospec" / "continuity.json"
            _ = marker.write_bytes(exact_bytes)
            self.assertEqual(store.load(), exact)

            oversized = deepcopy(exact)
            oversized_documents = dict(oversized["documents"])
            _ = oversized_documents.pop(exact_path)
            oversized_documents[f"{exact_path[:-len('/spec.md')]}a/spec.md"] = "0" * 64
            oversized["documents"] = oversized_documents
            oversized_bytes = encode_json(cast(JsonValue, cast(object, oversized)))
            self.assertEqual(len(oversized_bytes), maximum + 1)
            _ = marker.write_bytes(oversized_bytes)
            with self.assertRaises(ContinuityError) as raised:
                _ = store.load()
            self.assertEqual(raised.exception.code, "CORRUPT_CONTINUITY")

    def test_oversized_continuity_state_is_rejected_without_mutation(self) -> None:
        with TemporaryDirectory(prefix="evospec-continuity-state-size-") as temporary:
            root = Path(temporary)
            store, current = self._seed_continuity(root)
            candidate = deepcopy(current)
            candidate["revision"] += 1
            feature_id = next(iter(current["features"]))
            candidate["session_features"] = {
                **current["session_features"],
                **{
                    f"ses_pad_{number:05d}_" + "x" * 240: feature_id
                    for number in range(18_000)
                },
            }
            marker = root / ".evospec" / "continuity.json"
            before_marker = marker.read_bytes()

            with self.assertRaises(ContinuityError) as raised:
                with store.transaction():
                    store.commit(current, candidate, {})

            self.assertEqual(raised.exception.code, "LIMIT_EXCEEDED")
            self.assertIn("4 MiB", str(raised.exception))
            self.assertEqual(marker.read_bytes(), before_marker)
            self.assertEqual(store.load(), current)

    def test_near_bound_interrupted_journal_recovers_to_next_valid_state(self) -> None:
        with TemporaryDirectory(prefix="evospec-continuity-near-bound-") as temporary:
            root = Path(temporary)
            store, current = self._seed_continuity(root)
            candidate = deepcopy(current)
            candidate["revision"] += 1
            content = b"\x01" * 348_000
            writes: dict[str, tuple[bytes, str | None]] = {
                relative: (content, None)
                for relative in (
                    "specs/999-size-bound/spec.md",
                    "specs/999-size-bound/plan.md",
                )
            }
            failing_store = ContinuityPersistence(root, FailSecondReplace())
            with self.assertRaises(ContinuityError) as raised:
                with failing_store.transaction():
                    failing_store.commit(current, candidate, writes)
            self.assertEqual(raised.exception.code, "RECOVERY_INCOMPLETE")

            marker = root / ".evospec" / "continuity.json"
            journal_bytes = marker.read_bytes()
            self.assertGreater(len(journal_bytes), 4_150_000)
            self.assertLessEqual(len(journal_bytes), 4 * 1024 * 1024)
            pending = store.load()
            if pending is None or pending["pending"] is None:
                self.fail("expected readable interrupted journal")
            self.assertEqual(pending["revision"], current["revision"])
            self.assertEqual(
                pending["pending"]["next_state"]["revision"],
                candidate["revision"],
            )
            with store.transaction():
                store.recover_pending(pending)
            recovered = store.load()
            self.assertIsNotNone(recovered)
            if recovered is None:
                return
            self.assertEqual(recovered["revision"], candidate["revision"])
            self.assertIsNone(recovered["pending"])
            for relative in writes:
                self.assertEqual((root / relative).read_bytes(), content)

    def test_composed_mode_and_legacy_state_writer_fail_closed_without_mixed_artifacts(self) -> None:
        with TemporaryDirectory(
            prefix="evospec-composed-legacy-barrier-",
            dir=os.environ.get("EVOSPEC_AC0_TEST_TMPDIR") or None,
        ) as temporary:
            root = Path(temporary)
            execute_continuity(root, "specify", 0, "ses_persist_barrier_123", {
                "title": "Composed only",
                "outcome": "Keep the new mode separate.",
                "criteria": [{
                    "description": "Atomic persistence tests pass.",
                    "check_kind": "python-unittest",
                    "check_target": "tests.test_persistence_atomic",
                }],
                "increments": [],
                "tasks": [{"title": "Write the mode barrier", "criteria": [0]}],
            })
            before = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            with self.assertRaises(RecoveryError):
                CoreService.open(root).recover()
            with self.assertRaises(RecoveryError):
                StateStore(root).load_state()
            after = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            self.assertEqual(before, after)

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

    def test_d4_proposal_collision_preserves_seeded_immutable_and_authority(self) -> None:
        with TemporaryDirectory(prefix="evospec-d4-collision-") as temporary:
            root = Path(temporary)
            workflow, source = _create_d4_session_request_fixture(root)
            assert source.initialization is not None
            feature_id = str(source.features[0].id)
            repository_id = source.initialization.repository_id
            enabled = workflow.activate_provisional_slice_writes(
                repository_id,
                feature_id,
                expected_revision=ExpectedRevision(source.revision),
            )
            current = CoreService.open(root).recover()
            assert current.active_context is not None
            before_unsupported = {
                path.relative_to(root / ".evospec"): path.read_bytes()
                for path in (root / ".evospec").rglob("*")
                if path.is_file()
            }
            with self.assertRaises(ServiceError):
                CoreService.open(root).record_safety_facts(
                    current,
                    True,
                    replace(
                        current.active_context.safety_facts,
                        unresolved_decision_details=("unsupported D4 safety mutation",),
                    ),
                )
            self.assertEqual(CoreService.open(root).recover(), current)
            self.assertEqual(before_unsupported, {
                path.relative_to(root / ".evospec"): path.read_bytes()
                for path in (root / ".evospec").rglob("*")
                if path.is_file()
            })

            title = "Deterministic collision"
            outcome = "A bounded suggestion"
            inclusions = ("A narrow scope",)
            exclusions = ("No acceptance implied",)
            considerations: tuple[str, ...] = ()
            open_questions: tuple[str, ...] = ()
            criteria = (ProvisionalSliceCriterion("c-1", "A visible outcome", "A focused check"),)
            revision = _new_d4_provisional_slice_revision(
                repository_id,
                feature_id,
                None,
                title,
                outcome,
                None,
                inclusions,
                exclusions,
                considerations,
                open_questions,
                criteria,
            )
            record_path = (
                root / ".evospec" / "records" / "provisional-slices"
                / f"{revision.revision_id}.json"
            )
            record_path.parent.mkdir(parents=True)
            preexisting = b'{"different":"immutable collision"}'
            record_path.write_bytes(preexisting)
            before = {
                path.relative_to(root / ".evospec"): path.read_bytes()
                for path in (root / ".evospec").rglob("*")
                if path.is_file()
            }

            with self.assertRaises(WorkflowError):
                workflow.propose_provisional_slice(
                    repository_id,
                    feature_id,
                    None,
                    title,
                    outcome,
                    None,
                    inclusions,
                    exclusions,
                    considerations,
                    open_questions,
                    criteria,
                    expected_revision=ExpectedRevision(current.revision),
                )

            recovered = CoreService.open(root).recover()
            self.assertEqual(recovered, current)
            self.assertEqual(recovered.revision, enabled.revision)
            self.assertEqual(recovered.provisional_slice_heads, ())
            self.assertEqual(recovered.provisional_slice_revisions, ())
            self.assertEqual(record_path.read_bytes(), preexisting)
            self.assertEqual(before, {
                path.relative_to(root / ".evospec"): path.read_bytes()
                for path in (root / ".evospec").rglob("*")
                if path.is_file()
            })

    def test_provisional_record_orphan_is_inert_when_state_replace_fails(self) -> None:
        with TemporaryDirectory(prefix="evospec-d1-atomic-") as temporary:
            root = Path(temporary)
            base = _create_provisional_fixture(root)
            state_path = root / ".evospec" / "state.json"
            before_state = state_path.read_bytes()
            revision = _make_provisional_revision(base)
            record_path = (
                root / ".evospec" / "records" / "provisional-slices"
                / f"{revision.revision_id}.json"
            )

            with self.assertRaises(AtomicFailure):
                _ProvisionalSliceTestHarness(root, FailSecondReplace()).publish(
                    base, revision, None,
                )

            self.assertEqual(state_path.read_bytes(), before_state)
            self.assertTrue(record_path.is_file())
            self.assertEqual(StateStore(root).load_state(), base)
            self.assertEqual(StateStore(root).load_state().provisional_slice_revisions, ())

            retried = _ProvisionalSliceTestHarness(root).publish(base, revision, None)
            self.assertEqual(retried.provisional_slice_revisions, (revision,))
            self.assertEqual(StateStore(root).load_state(), retried)

    def test_provisional_immutable_id_conflict_leaves_current_head_unchanged(self) -> None:
        with TemporaryDirectory(prefix="evospec-d1-conflict-") as temporary:
            root = Path(temporary)
            base = _create_provisional_fixture(root)
            store = _ProvisionalSliceTestHarness(root)
            first = _make_provisional_revision(base)
            state1 = store.publish(base, first, None)
            head1 = state1.provisional_slice_heads[0]
            candidate = _make_provisional_revision(
                state1, previous=first, outcome="candidate content",
            )
            conflict = _make_provisional_revision(
                state1, previous=first, outcome="different immutable content",
            )
            conflict_path = (
                root / ".evospec" / "records" / "provisional-slices"
                / f"{candidate.revision_id}.json"
            )
            conflict_path.parent.mkdir(parents=True, exist_ok=True)
            conflict_path.write_bytes(encode_json(_provisional_slice_to_json(conflict)))
            state_path = root / ".evospec" / "state.json"
            before_state = state_path.read_bytes()

            with self.assertRaises(AtomicFailure):
                store.publish(state1, candidate, head1)

            self.assertEqual(state_path.read_bytes(), before_state)
            self.assertEqual(StateStore(root).load_state(), state1)

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
