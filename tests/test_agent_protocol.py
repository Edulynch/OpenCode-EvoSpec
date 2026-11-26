from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from evospec.agent_protocol import (
    _criteria_revision_json,
    _request_scoped_objective_json,
    execute,
)
from evospec.discovery import MAX_EXTERNAL_SDD_COMMAND_ENTRIES
from evospec.domain import (
    CONTINUITY_INPUT_POLICY_DIGEST,
    CONTINUITY_INPUT_POLICY_ID,
    Change,
    ChangeId,
    ExpectedRevision,
    Feature,
    FeatureId,
    LivingSpecId,
    LockId,
    OperationalState,
    ReviewResult,
    SemanticLock,
    SemanticLockRevision,
    SliceCriterion,
    SliceCriteriaRevision,
    VerificationEvidence,
    WorkRelation,
    WorkRelationKind,
)
from evospec.lifecycle import Classification, ReviewVerdict
from evospec.persistence import JsonValue, StateStore
from evospec.services import CoreService, RequestScopedObjectiveStatus
from evospec.workflow import WorkflowError, WorkflowService
from tests._tempdir import TemporaryDirectory
from tests.test_persistence_atomic import FailingReplace
from tests.test_services_state import convergence_assessment


def request(action: str, **values: JsonValue) -> str:
    payload: dict[str, JsonValue] = {"action": action}
    payload.update(values)
    return json.dumps(payload, ensure_ascii=False)


def assessment(
    operation: str, capability_state: str, classification: str, feature_id: str | None = None
) -> dict[str, JsonValue]:
    return {
        "operation": operation,
        "classification": classification,
        "confidence": "HIGH",
        "capability_state": capability_state,
        "selected_capability": "Export capability",
        "candidate_capabilities": [],
        "selected_feature_id": feature_id,
        "evidence": [{"source": "agent", "reference": "tests/test_agent_protocol.py"}],
        "material_ambiguities": [],
        "recommended_depth": {"review": "NORMAL", "verification": "NORMAL", "convergence": "NORMAL"},
        "rationale": "attributable protocol assessment",
    }


def activate_side(root: Path) -> int:
    core = CoreService.open(root)
    primary = core.recover().active_context
    if primary is None:
        raise AssertionError("expected active primary")
    feature = Feature(FeatureId("feature-side"), "Side capability", LivingSpecId("spec-feature-side"), primary.feature_id)
    state = core.record_feature(core.recover(), feature)
    return core.activate_side(state, Change(ChangeId("change-side"), feature.id, "unrelated side capability", Classification.PATCH)).revision


class AgentProtocolTests(unittest.TestCase):
    def test_composed_continuity_uses_the_provider_neutral_protocol_without_legacy_init(self) -> None:
        with TemporaryDirectory(
            prefix="evospec-continuity-protocol-",
            dir=os.environ.get("EVOSPEC_AC0_TEST_TMPDIR") or None,
        ) as temporary:
            root = Path(temporary)
            request_data: dict[str, JsonValue] = {
                "title": "Protocol Feature",
                "outcome": "Persist a composed outcome.",
                "criteria": [{
                    "description": "The protocol test passes.",
                    "check_kind": "python-unittest",
                    "check_target": "tests.test_agent_protocol",
                }],
                "increments": [],
                "tasks": [{
                    "title": "Implement the protocol path",
                    "description": "Use the shared Core operation.",
                    "criteria": [0],
                }],
                "plan": "One short unsliced correction path.",
            }
            response = json.loads(execute(root, request(
                "continuity", operation="specify", expected_revision=0,
                session_id="ses_protocol_continuity_123", data=request_data,
            )))
            self.assertNotIn("error", response)
            continuity = response["continuity"]
            self.assertEqual(continuity["mode"], "COMPOSED")
            self.assertEqual(continuity["revision"], 1)
            self.assertEqual(continuity["selected_feature"]["increments"], [])
            self.assertEqual(
                json.loads(execute(root, request(
                    "continuity", operation="status", session_id="ses_protocol_continuity_123",
                )))["continuity"]["selected_feature"]["id"],
                continuity["selected_feature"]["id"],
            )
            feature = continuity["selected_feature"]
            feature_id = feature["id"]
            task_id = feature["tasks"][0]["id"]
            pause_reason = "Pause for a separately tracked dependency."
            detour_title = "Resolve the external dependency"
            paused_response = json.loads(execute(root, request(
                "continuity", operation="pause",
                expected_revision=continuity["revision"],
                session_id="ses_protocol_continuity_123",
                data={
                    "reason": pause_reason,
                    "detour_kind": "EXTERNAL",
                    "detour_title": detour_title,
                    "return_task_id": task_id,
                },
            )))
            self.assertNotIn("error", paused_response)
            paused = paused_response["continuity"]
            self.assertEqual(paused["revision"], continuity["revision"] + 1)
            paused_feature = paused["selected_feature"]
            self.assertTrue(paused_feature["paused"])
            self.assertEqual(paused_feature["pause_reason"], pause_reason)
            detour = paused_feature["detours"][0]
            self.assertEqual(
                (detour["feature_id"], detour["kind"], detour["title"],
                 detour["return_task_id"], detour["result"]),
                (feature_id, "EXTERNAL", detour_title, task_id, None),
            )

            recovery_session = "ses_protocol_continuity_recovery_456"
            before_status = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            recovered = json.loads(execute(root, request(
                "continuity", operation="status", session_id=recovery_session,
            )))["continuity"]
            after_status = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            self.assertEqual(before_status, after_status)
            self.assertEqual(recovered["selection_scope"], "worktree")
            self.assertEqual(recovered["selected_feature_id"], feature_id)
            self.assertTrue(recovered["selected_feature"]["paused"])
            self.assertEqual(recovered["selected_feature"]["pause_reason"], pause_reason)

            detour_summary = "The external dependency is resolved; resume the original Task."
            resumed_response = json.loads(execute(root, request(
                "continuity", operation="resume",
                expected_revision=paused["revision"],
                session_id=recovery_session,
                data={"feature_id": feature_id, "detour_summary": detour_summary},
            )))
            self.assertNotIn("error", resumed_response)
            resumed = resumed_response["continuity"]
            self.assertEqual(resumed["revision"], paused["revision"] + 1)
            self.assertFalse(resumed["selected_feature"]["paused"])
            self.assertIsNone(resumed["selected_feature"]["pause_reason"])
            resumed_detour = resumed["selected_feature"]["detours"][0]
            self.assertEqual(resumed_detour["result"], detour_summary)
            self.assertEqual(resumed_detour["reported_by_session_id"], recovery_session)
            self.assertEqual(resumed_detour["resolved_revision"], resumed["revision"])
            before_status = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            after_resume = json.loads(execute(root, request(
                "continuity", operation="status",
                session_id="ses_protocol_continuity_after_resume_789",
            )))["continuity"]
            after_status = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            self.assertEqual(before_status, after_status)
            self.assertEqual(after_resume["selected_feature_id"], feature_id)
            self.assertFalse(after_resume["selected_feature"]["paused"])
            self.assertEqual(
                after_resume["selected_feature"]["detours"][0]["result"],
                detour_summary,
            )

            invalid = json.loads(execute(root, request(
                "continuity", operation="status", session_id="model-controlled",
                unexpected="field",
            )))
            self.assertEqual(invalid["error"]["code"], "INVALID_REQUEST")

    def test_composed_protocol_rejects_stale_input_snapshot_for_same_check_descriptor(self) -> None:
        with TemporaryDirectory(
            prefix="evospec-continuity-freshness-protocol-",
            dir=os.environ.get("EVOSPEC_AC0_TEST_TMPDIR") or None,
        ) as temporary:
            root = Path(temporary)
            session_id = "ses_protocol_freshness_123"
            created = json.loads(execute(root, request(
                "continuity",
                operation="specify",
                expected_revision=0,
                session_id=session_id,
                data={
                    "title": "Protocol freshness",
                    "outcome": "Checks remain fresh only for their exact inputs.",
                    "criteria": [{
                        "description": "The linked Python protocol Task passes.",
                        "check_kind": "python-unittest",
                        "check_target": "tests.test_agent_protocol",
                    }],
                    "increments": [],
                    "tasks": [{
                        "title": "Implement the protocol path",
                        "description": "Keep the check bound to this Task.",
                        "criteria": [0],
                    }],
                    "plan": "Implement, snapshot, verify, and recheck on input change.",
                },
            )))
            self.assertNotIn("error", created)
            continuity = created["continuity"]
            feature = continuity["selected_feature"]
            task = feature["tasks"][0]
            criterion = feature["criteria"][0]
            self.assertIn(criterion["id"], task["criteria_ids"])

            started = json.loads(execute(root, request(
                "continuity",
                operation="implement",
                expected_revision=continuity["revision"],
                session_id=session_id,
                data={"task_id": task["id"], "phase": "begin"},
            )))
            self.assertNotIn("error", started)
            reported = json.loads(execute(root, request(
                "continuity",
                operation="implement",
                expected_revision=started["continuity"]["revision"],
                session_id=session_id,
                data={
                    "task_id": task["id"],
                    "phase": "report",
                    "result": "SUCCEEDED",
                    "report": "The linked protocol Task is implemented.",
                },
            )))
            self.assertNotIn("error", reported)

            source = root / "src" / "implementation.py"
            source.parent.mkdir()
            source.write_text("value = 1\n", encoding="utf-8")
            before_snapshot_files = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            snapshot_response = json.loads(execute(root, request(
                "continuity",
                operation="input_snapshot",
                expected_revision=reported["continuity"]["revision"],
                session_id=session_id,
            )))
            after_snapshot_files = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            self.assertNotIn("error", snapshot_response)
            self.assertEqual(before_snapshot_files, after_snapshot_files)
            snapshot_continuity = snapshot_response["continuity"]
            snapshot = snapshot_continuity["input_snapshot"]
            self.assertEqual(snapshot_continuity["revision"], reported["continuity"]["revision"])
            self.assertEqual(snapshot["policy_id"], CONTINUITY_INPUT_POLICY_ID)
            self.assertEqual(snapshot["policy_digest"], CONTINUITY_INPUT_POLICY_DIGEST)
            self.assertRegex(snapshot["fingerprint"], r"^[0-9a-f]{64}$")

            verify_data: dict[str, JsonValue] = {
                "feature_id": feature["id"],
                "criterion_id": criterion["id"],
                "task_id": task["id"],
                "check_kind": criterion["check_kind"],
                "check_target": criterion["check_target"],
                "exit_code": 0,
                "output_sha256": sha256(b"protocol check passed").hexdigest(),
                "input_snapshot_before": snapshot,
                "input_snapshot_after": snapshot,
            }
            verified_response = json.loads(execute(root, request(
                "continuity",
                operation="verify",
                expected_revision=snapshot_continuity["revision"],
                session_id=session_id,
                data=verify_data,
            )))
            self.assertNotIn("error", verified_response)
            verified = verified_response["continuity"]
            self.assertTrue(verified["selected_feature"]["criteria"][0]["check"]["fresh"])
            self.assertEqual(verified["selected_feature"]["tasks"][0]["status"], "COMPLETED")

            source.write_text("value = 2\n", encoding="utf-8")
            changed_snapshot_response = json.loads(execute(root, request(
                "continuity",
                operation="input_snapshot",
                expected_revision=verified["revision"],
                session_id=session_id,
            )))
            self.assertNotIn("error", changed_snapshot_response)
            changed_snapshot = changed_snapshot_response["continuity"]["input_snapshot"]
            self.assertNotEqual(changed_snapshot["fingerprint"], snapshot["fingerprint"])
            stale_verify_data = {
                **verify_data,
                "input_snapshot_after": changed_snapshot,
            }
            stale_verify = json.loads(execute(root, request(
                "continuity",
                operation="verify",
                expected_revision=verified["revision"],
                session_id=session_id,
                data=stale_verify_data,
            )))
            self.assertEqual(stale_verify["error"]["code"], "STALE_INPUT")

            current = json.loads(execute(root, request(
                "continuity", operation="status", session_id=session_id,
            )))["continuity"]
            stale_check = current["selected_feature"]["criteria"][0]["check"]
            self.assertEqual(current["revision"], verified["revision"])
            self.assertFalse(stale_check["fresh"])
            self.assertFalse(stale_check["passed"])
            self.assertTrue(stale_check["historical_passed"])
            self.assertEqual(stale_check["stale_reason"], "input_changed")
            self.assertEqual(current["selected_feature"]["tasks"][0]["status"], "REPORTED")

    def test_d4_protocol_activates_and_projects_only_core_current_provisional_head(self) -> None:
        with TemporaryDirectory(prefix="evospec-d4-protocol-") as temporary:
            root = Path(temporary)
            initialized = json.loads(execute(root, request("init", expected_revision=0)))
            repository_id = sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
            session_id = "ses_d4protocol123"
            message_id = "msg_d4protocol123"
            admitted = json.loads(execute(
                root,
                request(
                    "accept_first_slice_session",
                    expected_revision=initialized["result"]["revision"],
                    repository_id=repository_id,
                    session_id=session_id,
                    message_id=message_id,
                    request_text="Implement the requested bounded Feature.",
                ),
            ))
            self.assertEqual(admitted["result"]["code"], "SESSION_SLICE_RECORDED")
            self.assertTrue(admitted["context"]["provisional_slice_activation_available"])
            self.assertFalse(admitted["context"]["provisional_slice_write_ready"])
            self.assertIn("activate_provisional_slice_writes", admitted["context"]["legal_actions"])
            self.assertNotIn("propose_provisional_slice", admitted["context"]["legal_actions"])
            feature_id = admitted["context"]["feature_id"]

            activated = json.loads(execute(
                root,
                request(
                    "activate_provisional_slice_writes",
                    expected_revision=admitted["result"]["revision"],
                    repository_id=repository_id,
                    feature_id=feature_id,
                ),
            ))
            self.assertEqual(activated["result"]["code"], "PROVISIONAL_SLICE_WRITES_ACTIVATED")
            self.assertEqual(activated["result"]["revision"], admitted["result"]["revision"] + 1)
            self.assertFalse(activated["context"]["provisional_slice_activation_available"])
            self.assertTrue(activated["context"]["provisional_slice_write_ready"])
            self.assertIn("propose_provisional_slice", activated["context"]["legal_actions"])
            self.assertEqual(
                set(activated["context"]["legal_actions"]),
                {
                    "context",
                    "list_work",
                    "next_work",
                    "accepted_task_progress",
                    "propose_provisional_slice",
                },
            )
            self.assertEqual(activated["context"]["provisional_slice_inventory"]["total"], 0)
            self.assertEqual(json.loads((root / ".evospec" / "state.json").read_bytes())["schema"], 23)

            before_reject = CoreService.open(root).recover()
            before_artifacts = {
                path.relative_to(root / ".evospec"): path.read_bytes()
                for path in (root / ".evospec").rglob("*")
                if path.is_file()
            }
            workflow_reject = WorkflowService.open(root).reject(
                expected_revision=ExpectedRevision(before_reject.revision),
            )
            self.assertEqual(workflow_reject.code, "D4_SCHEMA23_ACTION_UNSUPPORTED")
            protocol_reject = json.loads(execute(
                root,
                request("reject", expected_revision=before_reject.revision),
            ))
            self.assertEqual(
                protocol_reject["result"]["code"],
                "D4_SCHEMA23_ACTION_UNSUPPORTED",
            )
            self.assertEqual(CoreService.open(root).recover(), before_reject)
            self.assertEqual(
                before_artifacts,
                {
                    path.relative_to(root / ".evospec"): path.read_bytes()
                    for path in (root / ".evospec").rglob("*")
                    if path.is_file()
                },
            )

            first = json.loads(execute(
                root,
                request(
                    "propose_provisional_slice",
                    expected_revision=activated["result"]["revision"],
                    repository_id=repository_id,
                    feature_id=feature_id,
                    expected_head=None,
                    title="Suggested next Slice",
                    outcome="A bounded proposed outcome",
                    example=None,
                    inclusions=["Keep this future work bounded"],
                    exclusions=["Do not infer Slice acceptance"],
                    considerations=[],
                    open_questions=["Which final detail should be chosen?"],
                    criteria=[{
                        "id": "c-1",
                        "expected_result": "A concrete future result",
                        "expected_evidence": "A visible check",
                    }],
                ),
            ))
            self.assertEqual(first["result"]["code"], "PROVISIONAL_SLICE_PROPOSED")
            self.assertEqual(first["result"]["revision"], activated["result"]["revision"] + 1)
            self.assertEqual(first["context"]["provisional_slice_inventory"]["total"], 1)
            first_item = first["context"]["provisional_slice_inventory"]["items"][0]
            first_head = first_item["head"]
            self.assertEqual(first_item["provenance"], "AGENT_SUGGESTION")
            self.assertEqual(first_item["status"], "PROVISIONAL")

            second = json.loads(execute(
                root,
                request(
                    "propose_provisional_slice",
                    expected_revision=first["result"]["revision"],
                    repository_id=repository_id,
                    feature_id=feature_id,
                    expected_head={
                        "proposal_id": first_item["proposal_id"],
                        **first_head,
                    },
                    title="Suggested next Slice",
                    outcome="A clarified bounded outcome",
                    example=None,
                    inclusions=["Keep this future work bounded"],
                    exclusions=["Do not infer Slice acceptance"],
                    considerations=[],
                    open_questions=["Which final detail should be chosen?"],
                    criteria=[{
                        "id": "c-1",
                        "expected_result": "A concrete future result",
                        "expected_evidence": "A visible check",
                    }],
                ),
            ))
            self.assertEqual(second["result"]["code"], "PROVISIONAL_SLICE_PROPOSED")
            self.assertEqual(second["result"]["revision"], first["result"]["revision"] + 1)
            inventory = second["context"]["provisional_slice_inventory"]
            self.assertEqual(inventory["total"], 1)
            self.assertEqual(inventory["items"][0]["head"]["revision"], 2)
            self.assertNotEqual(inventory["items"][0]["head"]["revision_id"], first_head["revision_id"])
            self.assertEqual(second["context"]["accepted_slice"]["source"], "SESSION_REQUEST")
            self.assertNotIn("provisional_slice", second["context"]["slice_acceptance_inventory"]["items"][0])

            before_progress = CoreService.open(root).recover()
            accepted = second["context"]["accepted_slice"]
            self.assertIn(
                "accepted_task_progress",
                second["context"]["legal_actions"],
            )
            before_report_files = {
                path.relative_to(root / ".evospec"): path.read_bytes()
                for path in (root / ".evospec").rglob("*")
                if path.is_file()
            }
            generic_progress = json.loads(execute(
                root,
                request(
                    "progress",
                    expected_revision=before_progress.revision,
                    current_action="implement",
                    focus="the accepted request",
                    completed_work=[],
                    current_work="attempt legacy progress",
                    pending_work=[],
                    resume_point="resume",
                ),
            ))
            premature_review = json.loads(execute(
                root,
                request(
                    "review",
                    expected_revision=before_progress.revision,
                    review={"verdict": "PASS", "findings": [], "safe_to_proceed": True},
                ),
            ))
            self.assertEqual(generic_progress["error"]["code"], "WORKFLOW_ERROR")
            self.assertEqual(
                premature_review["result"]["code"],
                "ACCEPTED_TASK_PROGRESS_REQUIRED",
            )
            self.assertEqual(CoreService.open(root).recover(), before_progress)
            self.assertEqual(
                before_report_files,
                {
                    path.relative_to(root / ".evospec"): path.read_bytes()
                    for path in (root / ".evospec").rglob("*")
                    if path.is_file()
                },
            )
            progressed = json.loads(execute(
                root,
                request(
                    "accepted_task_progress",
                    expected_revision=second["result"]["revision"],
                    repository_id=repository_id,
                    task_id=accepted["task_id"],
                    feature_agreement_ref=accepted["feature_agreement_ref"],
                    slice_agreement_ref=accepted["slice_agreement_ref"],
                    checkpoint="Initial implementation step",
                    statement="Recorded an unverified implementation update.",
                    evidence_refs=["local observation"],
                ),
            ))
            after_progress = CoreService.open(root).recover()
            self.assertEqual(progressed["result"]["code"], "TASK_PROGRESS_RECORDED")
            self.assertEqual(json.loads((root / ".evospec" / "state.json").read_bytes())["schema"], 23)
            self.assertEqual(after_progress.provisional_slice_activation, before_progress.provisional_slice_activation)
            self.assertEqual(after_progress.provisional_slice_revisions, before_progress.provisional_slice_revisions)
            self.assertEqual(after_progress.provisional_slice_heads, before_progress.provisional_slice_heads)
            self.assertEqual(after_progress.agreements, before_progress.agreements)
            self.assertEqual(progressed["context"]["provisional_slice_inventory"]["items"][0]["head"]["revision"], 2)
            self.assertTrue(progressed["context"]["provisional_slice_write_ready"])

            before_review = CoreService.open(root).recover()
            assert before_review.active_context is not None
            assert before_review.active_context.change_id is not None
            before_review_files = {
                path.relative_to(root / ".evospec"): path.read_bytes()
                for path in (root / ".evospec").rglob("*")
                if path.is_file()
            }
            premature_verify = json.loads(execute(
                root,
                request(
                    "verify",
                    expected_revision=before_review.revision,
                    verification={"evidence": ["tests"], "sufficient": True},
                ),
            ))
            premature_converge = json.loads(execute(
                root,
                request(
                    "converge",
                    expected_revision=before_review.revision,
                    assessment={
                        "change_id": str(before_review.active_context.change_id),
                        "product_truth_changed": False,
                        "confidence": "HIGH",
                        "evidence": [{"source": "agent", "reference": "tests/test_agent_protocol.py"}],
                        "rationale": "premature convergence",
                    },
                ),
            ))
            self.assertEqual(premature_verify["result"]["code"], "REVIEW_REQUIRED")
            self.assertEqual(premature_converge["result"]["code"], "REVIEW_REQUIRED")
            self.assertEqual(CoreService.open(root).recover(), before_review)
            self.assertEqual(
                before_review_files,
                {
                    path.relative_to(root / ".evospec"): path.read_bytes()
                    for path in (root / ".evospec").rglob("*")
                    if path.is_file()
                },
            )

            reviewed = json.loads(execute(
                root,
                request(
                    "review",
                    expected_revision=progressed["result"]["revision"],
                    review={"verdict": "PASS", "findings": [], "safe_to_proceed": True},
                ),
            ))
            self.assertEqual(reviewed["result"]["code"], "REVIEWED")
            self.assertEqual(reviewed["result"]["revision"], progressed["result"]["revision"] + 2)
            before_verification = CoreService.open(root).recover()
            assert before_verification.active_context is not None
            assert before_verification.active_context.change_id is not None
            premature_converge = json.loads(execute(
                root,
                request(
                    "converge",
                    expected_revision=before_verification.revision,
                    assessment={
                        "change_id": str(before_verification.active_context.change_id),
                        "product_truth_changed": False,
                        "confidence": "HIGH",
                        "evidence": [{"source": "agent", "reference": "tests/test_agent_protocol.py"}],
                        "rationale": "verification is still required",
                    },
                ),
            ))
            self.assertEqual(premature_converge["result"]["code"], "VERIFICATION_REQUIRED")
            self.assertEqual(CoreService.open(root).recover(), before_verification)

            verified = json.loads(execute(
                root,
                request(
                    "verify",
                    expected_revision=reviewed["result"]["revision"],
                    verification={"evidence": ["focused checks passed"], "sufficient": True},
                ),
            ))
            self.assertEqual(verified["result"]["code"], "VERIFIED")
            self.assertEqual(verified["result"]["revision"], reviewed["result"]["revision"] + 2)
            change_id = verified["result"]["change_id"]
            if type(change_id) is not str:
                self.fail("expected active D4 Change identifier")
            false_assessment: dict[str, JsonValue] = {
                "change_id": change_id,
                "product_truth_changed": False,
                "confidence": "HIGH",
                "evidence": [{"source": "agent", "reference": "tests/test_agent_protocol.py"}],
                "rationale": "accepted terminal work does not publish Product Truth",
            }
            invalid_assessments: tuple[dict[str, JsonValue], ...] = (
                {
                    **false_assessment,
                    "product_truth_changed": True,
                    "product_truth_content": "# Must not publish\n",
                },
                {
                    **false_assessment,
                    "product_truth_content": "# Content is not allowed with false convergence\n",
                },
            )
            before_bad_convergence = CoreService.open(root).recover()
            before_bad_convergence_files = {
                path.relative_to(root / ".evospec"): path.read_bytes()
                for path in (root / ".evospec").rglob("*")
                if path.is_file()
            }
            for invalid_assessment in invalid_assessments:
                rejected = json.loads(execute(
                    root,
                    request(
                        "converge",
                        expected_revision=before_bad_convergence.revision,
                        assessment=invalid_assessment,
                    ),
                ))
                self.assertEqual(rejected["error"]["code"], "WORKFLOW_ERROR")
                self.assertEqual(CoreService.open(root).recover(), before_bad_convergence)
                self.assertEqual(
                    before_bad_convergence_files,
                    {
                        path.relative_to(root / ".evospec"): path.read_bytes()
                        for path in (root / ".evospec").rglob("*")
                        if path.is_file()
                    },
                )

            converged = json.loads(execute(
                root,
                request(
                    "converge",
                    expected_revision=verified["result"]["revision"],
                    assessment=false_assessment,
                ),
            ))
            closed = CoreService.open(root).recover()
            self.assertEqual(converged["result"]["code"], "DONE")
            self.assertEqual(converged["result"]["revision"], verified["result"]["revision"] + 3)
            self.assertEqual(json.loads((root / ".evospec" / "state.json").read_bytes())["schema"], 23)
            self.assertIsNone(closed.active_context)
            self.assertIsNone(closed.active_work_id)
            self.assertEqual(len(closed.features), 1)
            self.assertEqual(len(closed.changes), 1)
            self.assertEqual(closed.features, before_progress.features)
            self.assertEqual(closed.changes, before_progress.changes)
            self.assertEqual(closed.agreements, before_progress.agreements)
            self.assertEqual(len(closed.agreements), 2)
            self.assertEqual(closed.feature_completion_receipts, ())
            self.assertEqual(closed.feature_reopen_receipts, ())
            self.assertEqual(closed.slice_outcome_receipts, ())
            self.assertEqual(closed.provisional_slice_activation, before_progress.provisional_slice_activation)
            self.assertEqual(closed.provisional_slice_revisions, before_progress.provisional_slice_revisions)
            self.assertEqual(closed.provisional_slice_heads, before_progress.provisional_slice_heads)
            self.assertEqual(closed.publications, ())
            self.assertEqual(closed.publication_history, ())
            self.assertEqual(len(closed.terminal_archives), 1)
            archive = closed.terminal_archives[0]
            self.assertEqual(archive.context.status.value, "ACCEPTED")
            self.assertEqual(archive.context.phase.value, "DONE")
            assert archive.context.task_progress is not None
            self.assertEqual(archive.context.task_progress.report_kind, "REPORTED_UNVERIFIED")
            assert archive.convergence_assessment is not None
            self.assertFalse(archive.convergence_assessment.product_truth_changed)
            self.assertIsNone(archive.convergence_assessment.product_truth_content)
            self.assertEqual(len(closed.work_items), 1)
            self.assertEqual(closed.work_items[0].operational_state.value, "DONE")
            self.assertEqual(closed.work_items[0].terminal_change_id, archive.context.change_id)
            self.assertEqual(
                (root / ".evospec" / "records" / "provisional-slices"
                 / f"{first_head['revision_id']}.json").read_bytes(),
                before_report_files[
                    Path("records") / "provisional-slices" / f"{first_head['revision_id']}.json"
                ],
            )
            closed_files = {
                path.relative_to(root / ".evospec"): path.read_bytes()
                for path in (root / ".evospec").rglob("*")
                if path.is_file()
            }
            self.assertEqual(
                {
                    path: data for path, data in before_report_files.items()
                    if path.parts[:2] == ("records", "provisional-slices")
                },
                {
                    path: data for path, data in closed_files.items()
                    if path.parts[:2] == ("records", "provisional-slices")
                },
            )
            self.assertEqual(StateStore(root).load_state(), closed)

    def test_request_scoped_objective_protocol_verifies_closes_and_recovers(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialized = json.loads(execute(root, request("init", expected_revision=0)))
            self.assertIsNone(initialized["context"]["request_scoped_objective"])
            repository_id = sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
            request_text = 'Create a static HTML page with the heading "Hello".'
            session_id = "ses_objective123"
            message_id = "msg_objective123"
            admitted = json.loads(execute(
                root,
                request(
                    "accept_first_slice_session",
                    expected_revision=initialized["result"]["revision"],
                    repository_id=repository_id,
                    session_id=session_id,
                    message_id=message_id,
                    request_text=request_text,
                ),
            ))
            self.assertEqual(admitted["result"]["code"], "SESSION_SLICE_RECORDED")
            self.assertEqual(
                admitted["result"]["revision"],
                initialized["result"]["revision"] + 1,
            )
            self.assertEqual(
                admitted["context"]["request_scoped_objective"]["status"],
                "BLOCKED",
            )
            self.assertIn(
                "REQUEST_OBJECTIVE_ARTIFACT_MISSING",
                admitted["context"]["request_scoped_objective"]["blockers"],
            )
            (root / "index.html").write_text(
                "<!doctype html><html><head></head><body>"
                "<h1>Hello</h1></body></html>",
                encoding="utf-8",
            )
            ready = json.loads(execute(root, request("context")))["context"]
            self.assertEqual(ready["request_scoped_objective"]["status"], "PENDING")
            self.assertNotIn(
                "REQUEST_OBJECTIVE_CONTENT_NOT_VERIFIED",
                ready["request_scoped_objective"]["blockers"],
            )
            _, snapshot = WorkflowService.open(root).input_snapshot(
                expected_revision=ExpectedRevision(ready["revision"]),
            )
            fields: dict[str, JsonValue] = {
                "repository_id": repository_id,
                "session_id": session_id,
                "message_id": message_id,
                "request_text": request_text,
                "input_fingerprint": snapshot.fingerprint,
            }

            verified = json.loads(execute(
                root,
                request(
                    "request_scoped_objective",
                    expected_revision=ready["revision"],
                    **fields,
                ),
            ))

            self.assertEqual(verified["result"]["code"], "REQUEST_OBJECTIVE_VERIFIED")
            self.assertEqual(
                verified["result"]["revision"],
                ready["revision"] + 1,
            )
            proof = verified["context"]["request_scoped_objective"]
            self.assertEqual(
                set(proof),
                {"status", "source_reference", "request_sha256", "blockers", "heading"},
            )
            self.assertEqual(proof["status"], "VERIFIED")
            self.assertEqual(proof["source_reference"], (
                f"session:{session_id}:{message_id}:"
                f"{sha256(request_text.encode('utf-8')).hexdigest()[:16]}"
            ))
            self.assertEqual(proof["request_sha256"], sha256(request_text.encode("utf-8")).hexdigest())
            self.assertEqual(proof["heading"], "Hello")
            self.assertEqual(proof["blockers"], [])
            criteria = verified["context"]["accepted_slice"]["criteria_revision"]
            self.assertEqual(criteria["source"], "REQUEST_SCOPED_OBJECTIVE")
            self.assertEqual(criteria["source_reference"], proof["source_reference"])
            self.assertEqual(verified["context"]["slice_outcome"]["status"], "CURRENT")
            self.assertNotEqual(verified["context"]["feature_completion"]["status"], "COMPLETE")
            self.assertEqual(
                verified["context"]["input_snapshot"]["fingerprint"],
                snapshot.fingerprint,
            )

            completed = json.loads(execute(
                root,
                request(
                    "request_scoped_objective",
                    expected_revision=verified["result"]["revision"],
                    **fields,
                ),
            ))
            self.assertEqual(completed["result"]["code"], "REQUEST_OBJECTIVE_COMPLETE")
            self.assertEqual(
                completed["result"]["revision"],
                verified["result"]["revision"] + 1,
            )

            state_path = root / ".evospec" / "state.json"
            artifact_path = root / "index.html"
            state_before = CoreService.open(root).recover()
            state_bytes_before = state_path.read_bytes()
            state_mtime_before = state_path.stat().st_mtime_ns
            artifact_bytes_before = artifact_path.read_bytes()
            artifact_mtime_before = artifact_path.stat().st_mtime_ns

            recovered = json.loads(execute(root, request("context")))["context"]
            reopened = json.loads(execute(root, request("context")))["context"]
            self.assertEqual(recovered["request_scoped_objective"]["status"], "COMPLETE")
            self.assertEqual(
                recovered["provisional_slice_inventory"]["availability"], "AVAILABLE",
            )
            self.assertEqual(recovered["provisional_slice_inventory"]["total"], 0)
            self.assertEqual(recovered["feature_completion"]["status"], "COMPLETE")
            self.assertIsNotNone(recovered["feature_completion"]["receipt_id"])
            self.assertEqual(reopened["slice_acceptance_inventory"], recovered["slice_acceptance_inventory"])
            self.assertEqual(
                recovered["slice_acceptance_inventory"]["history_scope"],
                "AGREEMENT_RECORDS_ONLY_NONEXHAUSTIVE",
            )
            historical_item = recovered["slice_acceptance_inventory"]["items"][0]
            self.assertEqual(historical_item["scope_status"], "HISTORICAL")
            self.assertEqual(historical_item["historical_acceptance_status"], "ACCEPTED_PREVIOUSLY")
            historical = historical_item["historical_acceptance"]
            receipt = historical["receipt"]
            self.assertEqual(receipt["repository_id"], recovered["repository_id"])
            self.assertEqual(receipt["slice_agreement_ref"], historical_item["slice_agreement_ref"])
            self.assertEqual(
                receipt["criteria_revision_id"],
                historical["criteria_revision"]["id"],
            )
            self.assertEqual(
                {proof["id"] for proof in historical["criterion_evidence"]},
                set(receipt["evidence_receipt_ids"]),
            )
            state_after = CoreService.open(root).recover()
            self.assertEqual(state_after, state_before)
            self.assertEqual(state_after.revision, recovered["revision"])
            self.assertEqual(state_path.relative_to(root).as_posix(), ".evospec/state.json")
            self.assertEqual(state_path.read_bytes(), state_bytes_before)
            self.assertEqual(state_path.stat().st_mtime_ns, state_mtime_before)
            self.assertEqual(artifact_path.relative_to(root).as_posix(), "index.html")
            self.assertEqual(artifact_path.read_bytes(), artifact_bytes_before)
            self.assertEqual(artifact_path.stat().st_mtime_ns, artifact_mtime_before)
            self.assertEqual(
                recovered["input_snapshot"]["fingerprint"],
                snapshot.fingerprint,
            )
            revision = recovered["revision"]
            replay = json.loads(execute(
                root,
                request(
                    "request_scoped_objective",
                    expected_revision=revision,
                    **fields,
                ),
            ))
            self.assertEqual(replay["result"]["code"], "REQUEST_OBJECTIVE_ALREADY_COMPLETE")
            self.assertEqual(replay["result"]["revision"], revision)

    def test_request_objective_wire_adds_typed_fields_only_for_semantic_kind(self) -> None:
        legacy = _request_scoped_objective_json(RequestScopedObjectiveStatus(
            status="PENDING",
            source_reference="session:ses_test0001:msg_test0001:0123456789abcdef",
            request_sha256="a" * 64,
            blockers=(),
            heading="Hello",
        ))
        self.assertEqual(set(legacy), {
            "status", "source_reference", "request_sha256", "blockers", "heading",
        })
        incremental = _request_scoped_objective_json(RequestScopedObjectiveStatus(
            status="BLOCKED",
            source_reference="session:ses_test0001:msg_test0001:0123456789abcdef",
            request_sha256="c" * 64,
            blockers=("REQUEST_OBJECTIVE_UNSUPPORTED_REQUEST",),
            heading=None,
            mode="INCREMENTAL",
        ))
        self.assertEqual(incremental, {
            "status": "BLOCKED",
            "source_reference": "session:ses_test0001:msg_test0001:0123456789abcdef",
            "request_sha256": "c" * 64,
            "blockers": ["REQUEST_OBJECTIVE_UNSUPPORTED_REQUEST"],
            "heading": None,
            "mode": "INCREMENTAL",
        })
        semantic = _request_scoped_objective_json(RequestScopedObjectiveStatus(
            status="PENDING",
            source_reference="session:ses_test0001:msg_test0001:0123456789abcdef",
            request_sha256="b" * 64,
            blockers=(),
            heading=None,
            kind="PERSONAL_TASKS_WEB",
            criteria=(("create", "DERIVED_OBJECTIVE"), ("consult", "DERIVED_OBJECTIVE")),
        ))
        self.assertEqual(semantic, {
            "status": "PENDING",
            "source_reference": "session:ses_test0001:msg_test0001:0123456789abcdef",
            "request_sha256": "b" * 64,
            "blockers": [],
            "heading": None,
            "kind": "PERSONAL_TASKS_WEB",
            "criteria": [
                {"operation": "create", "provenance": "DERIVED_OBJECTIVE"},
                {"operation": "consult", "provenance": "DERIVED_OBJECTIVE"},
            ],
        })

    def test_criteria_revision_provenance_is_optional_on_wire(self) -> None:
        def revision(provenance: str | None) -> SliceCriteriaRevision:
            return SliceCriteriaRevision(
                "criteria-semantic",
                "agreement-slice",
                "repo-semantic",
                "Personal tasks work",
                "Add and consult tasks",
                ("index.html",),
                ("other files",),
                (),
                (SliceCriterion(
                    "request-objective-create",
                    "A task can be created",
                    "core-personal-tasks-browser-v1:index.html",
                    provenance,
                ),),
                "REQUEST_SCOPED_OBJECTIVE",
                "session:ses_semantic123:msg_semantic123:0123456789abcdef",
            )

        legacy_projection = _criteria_revision_json(revision(None))
        assert legacy_projection is not None
        legacy_criteria = legacy_projection["criteria"]
        assert isinstance(legacy_criteria, list)
        self.assertEqual(len(legacy_criteria), 1)
        legacy_criterion = legacy_criteria[0]
        assert isinstance(legacy_criterion, dict)
        self.assertEqual(set(legacy_criterion), {
            "id", "expected_result", "expected_evidence",
        })
        semantic_projection = _criteria_revision_json(
            revision("DERIVED_OBJECTIVE")
        )
        assert semantic_projection is not None
        semantic_criteria = semantic_projection["criteria"]
        assert isinstance(semantic_criteria, list)
        self.assertEqual(len(semantic_criteria), 1)
        semantic_criterion = semantic_criteria[0]
        assert isinstance(semantic_criterion, dict)
        self.assertEqual(set(semantic_criterion), {
            "id", "expected_result", "expected_evidence", "provenance",
        })
        self.assertEqual(
            semantic_criterion["provenance"],
            "DERIVED_OBJECTIVE",
        )

    def test_personal_tasks_objective_projection_is_additive(self) -> None:
        if not {"kind", "criteria"} <= RequestScopedObjectiveStatus.__dataclass_fields__.keys():
            self.skipTest("Core request-objective status projection has not added semantic fields")
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialized = json.loads(execute(root, request("init", expected_revision=0)))
            repository_id = sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
            request_text = "Build a web app to manage my personal tasks"
            admitted = json.loads(execute(
                root,
                request(
                    "accept_first_slice_session",
                    expected_revision=initialized["result"]["revision"],
                    repository_id=repository_id,
                    session_id="ses_semantic123",
                    message_id="msg_semantic123",
                    request_text=request_text,
                ),
            ))
            self.assertEqual(admitted["result"]["code"], "SESSION_SLICE_RECORDED")

            context = json.loads(execute(root, request("context")))["context"]
            objective = context["request_scoped_objective"]
            self.assertEqual(set(objective), {
                "status", "source_reference", "request_sha256", "blockers",
                "heading", "kind", "criteria",
            })
            self.assertEqual(objective["kind"], "PERSONAL_TASKS_WEB")
            self.assertIsNone(objective["heading"])
            self.assertEqual(
                objective["criteria"],
                [
                    {"operation": "create", "provenance": "DERIVED_OBJECTIVE"},
                    {"operation": "consult", "provenance": "DERIVED_OBJECTIVE"},
                ],
            )
            self.assertEqual(
                objective["request_sha256"],
                sha256(request_text.encode("utf-8")).hexdigest(),
            )

    def test_request_scoped_objective_protocol_rejects_bad_fields_stale_and_incomplete_page(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialized = json.loads(execute(root, request("init", expected_revision=0)))
            repository_id = sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
            request_text = 'Create a static HTML page with the heading "Hello".'
            admitted = json.loads(execute(
                root,
                request(
                    "accept_first_slice_session",
                    expected_revision=initialized["result"]["revision"],
                    repository_id=repository_id,
                    session_id="ses_objective123",
                    message_id="msg_objective123",
                    request_text=request_text,
                ),
            ))
            self.assertEqual(admitted["result"]["code"], "SESSION_SLICE_RECORDED")
            self.assertEqual(
                admitted["result"]["revision"],
                initialized["result"]["revision"] + 1,
            )
            self.assertEqual(
                admitted["context"]["request_scoped_objective"]["status"],
                "BLOCKED",
            )
            self.assertIn(
                "REQUEST_OBJECTIVE_ARTIFACT_MISSING",
                admitted["context"]["request_scoped_objective"]["blockers"],
            )
            (root / "index.html").write_text(
                "<!doctype html><html><head></head><body>"
                "<h1>Not Hello</h1></body></html>",
                encoding="utf-8",
            )
            blocked = json.loads(execute(root, request("context")))["context"]
            self.assertEqual(blocked["request_scoped_objective"]["status"], "BLOCKED")
            self.assertIn(
                "REQUEST_OBJECTIVE_CONTENT_NOT_VERIFIED",
                blocked["request_scoped_objective"]["blockers"],
            )
            self.assertEqual(blocked["accepted_slice"]["criteria_status"], "NOT_RECORDED")
            _, snapshot = WorkflowService.open(root).input_snapshot(
                expected_revision=ExpectedRevision(blocked["revision"]),
            )
            fields: dict[str, JsonValue] = {
                "repository_id": repository_id,
                "session_id": "ses_objective123",
                "message_id": "msg_objective123",
                "request_text": request_text,
                "input_fingerprint": snapshot.fingerprint,
            }
            before = CoreService.open(root).recover()

            malformed = json.loads(execute(
                root,
                request(
                    "request_scoped_objective",
                    expected_revision=before.revision,
                    **{**fields, "unexpected": True},
                ),
            ))
            invalid_fingerprint = json.loads(execute(
                root,
                request(
                    "request_scoped_objective",
                    expected_revision=before.revision,
                    **{**fields, "input_fingerprint": "A" * 64},
                ),
            ))
            stale = json.loads(execute(
                root,
                request(
                    "request_scoped_objective",
                    expected_revision=before.revision - 1,
                    **fields,
                ),
            ))
            incomplete = json.loads(execute(
                root,
                request(
                    "request_scoped_objective",
                    expected_revision=before.revision,
                    **fields,
                ),
            ))

            self.assertEqual(malformed["error"]["code"], "INVALID_REQUEST")
            self.assertEqual(invalid_fingerprint["error"]["code"], "INVALID_REQUEST")
            self.assertEqual(stale["error"]["code"], "STALE_REVISION")
            self.assertEqual(incomplete["error"]["code"], "WORKFLOW_ERROR")
            self.assertIn(
                "REQUEST_OBJECTIVE_CONTENT_NOT_VERIFIED",
                incomplete["error"]["detail"],
            )
            self.assertNotIn("context", incomplete)
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_request_scoped_objective_generic_request_does_not_create_criteria(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialized = json.loads(execute(root, request("init", expected_revision=0)))
            repository_id = sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
            request_text = "Create a todo application with a form and persistent storage."
            admitted = json.loads(execute(
                root,
                request(
                    "accept_first_slice_session",
                    expected_revision=initialized["result"]["revision"],
                    repository_id=repository_id,
                    session_id="ses_generic123",
                    message_id="msg_generic123",
                    request_text=request_text,
                ),
            ))
            self.assertEqual(admitted["result"]["code"], "SESSION_SLICE_RECORDED")
            self.assertEqual(
                admitted["result"]["revision"],
                initialized["result"]["revision"] + 1,
            )
            self.assertEqual(
                admitted["context"]["request_scoped_objective"]["status"],
                "BLOCKED",
            )
            self.assertEqual(
                admitted["context"]["request_scoped_objective"]["blockers"],
                ["REQUEST_OBJECTIVE_UNSUPPORTED_REQUEST"],
            )
            self.assertEqual(
                admitted["context"]["request_scoped_objective"]["mode"],
                "INCREMENTAL",
            )
            _, snapshot = WorkflowService.open(root).input_snapshot(
                expected_revision=ExpectedRevision(admitted["result"]["revision"]),
            )
            before = CoreService.open(root).recover()

            response = json.loads(execute(
                root,
                request(
                    "request_scoped_objective",
                    expected_revision=before.revision,
                    repository_id=repository_id,
                    session_id="ses_generic123",
                    message_id="msg_generic123",
                    request_text=request_text,
                    input_fingerprint=snapshot.fingerprint,
                ),
            ))

            self.assertTrue(
                "error" in response
                or response["result"]["code"] not in {
                    "REQUEST_OBJECTIVE_VERIFIED",
                    "REQUEST_OBJECTIVE_COMPLETE",
                    "REQUEST_OBJECTIVE_ALREADY_COMPLETE",
                }
            )
            state = CoreService.open(root).recover()
            self.assertEqual(state.slice_criteria_heads, ())
            self.assertEqual(state.slice_criteria_revisions, ())
            self.assertEqual(state.revision, before.revision)

    def test_session_request_round_trips_with_separate_workspace_fingerprint(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialized = json.loads(execute(root, request("init", expected_revision=0)))
            repository_id = sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
            text = "A task list.\r\n\r\n- Add one.\r\n- Mark it done.\r\n"
            accepted_response = json.loads(execute(
                root,
                request(
                    "accept_first_slice_session",
                    expected_revision=initialized["result"]["revision"],
                    repository_id=repository_id,
                    session_id="ses_protocol123",
                    message_id="msg_protocol123",
                    request_text=text,
                ),
            ))
            self.assertNotIn("error", accepted_response)
            self.assertEqual(accepted_response["result"]["code"], "SESSION_SLICE_RECORDED")

            state_before_context = CoreService.open(root).recover()
            recovered = json.loads(execute(root, request("context")))
            context = recovered["context"]
            accepted = context["accepted_slice"]
            first_projection = accepted_response["context"]["accepted_slice"]
            completion = context["feature_completion"]
            self.assertIsInstance(completion, dict)
            self.assertEqual(context["revision"], accepted_response["result"]["revision"])
            self.assertEqual(CoreService.open(root).recover(), state_before_context)
            self.assertEqual(accepted["feature_agreement_ref"], first_projection["feature_agreement_ref"])
            self.assertEqual(accepted["slice_agreement_ref"], first_projection["slice_agreement_ref"])
            self.assertEqual(accepted["task_id"], first_projection["task_id"])
            self.assertEqual(accepted["checkpoint"], first_projection["checkpoint"])
            self.assertEqual(accepted["source"], "SESSION_REQUEST")
            self.assertEqual(
                accepted["source_reference"],
                "session:ses_protocol123:msg_protocol123:" + sha256(text.encode("utf-8")).hexdigest()[:16],
            )
            self.assertEqual(accepted["criteria_status"], "NOT_RECORDED")
            self.assertIsNone(accepted["criteria_revision"])
            self.assertEqual(accepted["session_request"], {
                "statement": text,
                "sha256": sha256(text.encode("utf-8")).hexdigest(),
                "repository_id": repository_id,
            })
            self.assertEqual(accepted["initial_task"], "Implementar únicamente el resultado expresado en la petición")
            inventory_item = context["slice_acceptance_inventory"]["items"][0]
            self.assertEqual(inventory_item["authority_status"], "CANONICAL")
            self.assertEqual(inventory_item["scope_status"], "ACTIVE")
            self.assertEqual(context["continuation"]["status"], "ACTIVE_SLICE")
            self.assertEqual(
                context["continuation"]["active_slice"]["slice_agreement_ref"],
                accepted["slice_agreement_ref"],
            )
            self.assertEqual(context["continuation"]["task"]["task_id"], accepted["task_id"])
            self.assertEqual(context["input_snapshot"]["repository_id"], repository_id)
            self.assertIsNone(context["slice_outcome"])
            self.assertIsNone(context["criterion_verification"])
            self.assertEqual(completion["status"], "BLOCKED")
            self.assertIn("SCHEMA_17_REQUIRED", completion["blockers"])
            self.assertIn("FEATURE_WORK_NOT_TERMINAL", completion["blockers"])
            initial_fingerprint = context["input_snapshot"]["fingerprint"]
            initial_revision = context["revision"]
            initial_request = accepted["session_request"]
            state_before_drift_read = CoreService.open(root).recover()

            (root / "index.html").write_text("<main>changed workspace</main>\n", encoding="utf-8")
            changed = json.loads(execute(root, request("context")))
            self.assertEqual(CoreService.open(root).recover(), state_before_drift_read)
            self.assertEqual(changed["context"]["revision"], initial_revision)
            self.assertNotEqual(
                changed["context"]["input_snapshot"]["fingerprint"], initial_fingerprint,
            )
            self.assertEqual(changed["context"]["accepted_slice"]["session_request"], initial_request)
            self.assertEqual(changed["context"]["accepted_slice"]["criteria_status"], "NOT_RECORDED")
            self.assertIsNone(changed["context"]["slice_outcome"])
            self.assertIsNone(changed["context"]["criterion_verification"])
            self.assertFalse(changed["context"]["continuation"]["terminal_history"])
            self.assertEqual(changed["context"]["feature_completion"]["status"], "BLOCKED")

    def test_incremental_session_request_wire_preserves_raw_text_and_core_goal(self) -> None:
        with TemporaryDirectory(prefix="evospec-incremental-wire-") as temporary:
            root = Path(temporary).resolve()
            initialized = json.loads(execute(root, request("init", expected_revision=0)))
            repository_id = sha256(str(root).encode("utf-8")).hexdigest()[:16]
            session_id = "ses_incremental_wire1"
            message_id = "msg_incremental_wire1"
            request_text = (
                "Build a weekend trip planner with budget alerts and saved itineraries."
                "\t \u0085"
            )
            admitted = json.loads(execute(
                root,
                request(
                    "accept_first_slice_session",
                    expected_revision=initialized["result"]["revision"],
                    repository_id=repository_id,
                    session_id=session_id,
                    message_id=message_id,
                    request_text=request_text,
                ),
            ))
            self.assertEqual(admitted["result"]["code"], "SESSION_SLICE_RECORDED")

            wire = json.loads(execute(root, request("context")))["context"]
            accepted = wire["accepted_slice"]
            objective = wire["request_scoped_objective"]
            digest = sha256(request_text.encode("utf-8")).hexdigest()
            self.assertEqual(accepted["session_request"], {
                "statement": request_text,
                "sha256": digest,
                "repository_id": repository_id,
            })
            self.assertEqual(
                accepted["source_reference"],
                f"session:{session_id}:{message_id}:{digest[:16]}",
            )
            self.assertEqual(accepted["slice_title"], request_text.strip())
            self.assertEqual(wire["autonomy_envelope"]["goal"], accepted["slice_title"])
            self.assertNotEqual(wire["autonomy_envelope"]["goal"], request_text)
            self.assertEqual(objective["status"], "BLOCKED")
            self.assertEqual(objective["mode"], "INCREMENTAL")
            self.assertEqual(
                objective["blockers"],
                ["REQUEST_OBJECTIVE_UNSUPPORTED_REQUEST"],
            )
            self.assertEqual(objective["request_sha256"], digest)

    def test_session_request_context_fails_closed_when_workspace_scan_fails(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialized = json.loads(execute(root, request("init", expected_revision=0)))
            repository_id = sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
            with patch(
                "evospec.workflow.WorkflowService.input_snapshot",
                side_effect=WorkflowError("workspace scan failed"),
            ):
                accepted = json.loads(execute(
                    root,
                    request(
                        "accept_first_slice_session",
                        expected_revision=initialized["result"]["revision"],
                        repository_id=repository_id,
                        session_id="ses_scanfail123",
                        message_id="msg_scanfail123",
                        request_text="A bounded request.",
                    ),
            ))

            self.assertNotIn("error", accepted)
            self.assertEqual(accepted["result"]["code"], "SESSION_SLICE_RECORDED")
            self.assertIsNone(accepted["context"]["input_snapshot"])
            committed = CoreService.open(root).recover()
            self.assertEqual(committed.revision, accepted["result"]["revision"])
            self.assertEqual(len(committed.agreements), 2)
            before_context = CoreService.open(root).recover()
            with patch(
                "evospec.workflow.WorkflowService.input_snapshot",
                side_effect=WorkflowError("workspace scan failed"),
            ):
                response = json.loads(execute(root, request("context")))

            self.assertNotIn("error", response)
            context = response["context"]
            accepted_slice = context["accepted_slice"]
            completion = context["feature_completion"]
            self.assertEqual(context["revision"], committed.revision)
            self.assertIsNone(context["input_snapshot"])
            self.assertFalse(context["r3b_schema_ready"])
            self.assertEqual(accepted_slice["source"], "SESSION_REQUEST")
            self.assertEqual(accepted_slice["session_request"]["statement"], "A bounded request.")
            self.assertEqual(context["slice_acceptance_inventory"]["items"][0]["authority_status"], "CANONICAL")
            self.assertEqual(context["continuation"]["status"], "ACTIVE_SLICE")
            self.assertIsNone(context["criterion_verification"])
            self.assertIsNone(context["slice_outcome"])
            self.assertEqual(completion["status"], "BLOCKED")
            self.assertIn("INPUT_SNAPSHOT_UNAVAILABLE", completion["blockers"])
            self.assertEqual(before_context.criterion_test_receipts, ())
            self.assertEqual(before_context.slice_outcome_receipts, ())
            self.assertEqual(before_context.feature_completion_receipts, ())
            self.assertEqual(CoreService.open(root).recover(), before_context)

    def test_archived_b_raw_progress_omissions_remain_invalid_after_complete_intake(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialized = json.loads(execute(root, request("init", expected_revision=0)))
            intake = json.loads(execute(
                root,
                request(
                    "new", expected_revision=initialized["result"]["revision"],
                    intent="Add export capability", assessment=assessment("NEW", "PLANNED", "FEATURE"),
                ),
            ))
            before = CoreService.open(root).recover()

            responses = tuple(
                json.loads(execute(root, payload))
                for payload in (
                    request("progress", expected_revision=intake["result"]["revision"], current_action="implement", focus="export", current_work="write", resume_point="test"),
                    request("progress", expected_revision=intake["result"]["revision"], current_action="implement", focus="export", completed_work=[], current_work="write", resume_point="test"),
                    request("progress", expected_revision=intake["result"]["revision"], current_action="implement", focus="export", current_work="write", pending_work=[], resume_point="test"),
                )
            )

            self.assertTrue(all(response["error"]["code"] == "INVALID_REQUEST" for response in responses))
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_archived_b_malformed_new_replay_is_non_mutating_and_full_intake_succeeds(self) -> None:
        # Given: the archived B malformed natural-intake attempts and their complete replacement
        malformed_payloads = (
            '{"action":"new","expected_revision":1,"intent":"Add export capability"}',
            '{"action":"new","expected_revision":1,"intent":"Add export capability","assessment":null}',
            '{"action":"new","expected_revision":1,"intent":"Add export capability","assessment":[]}',
            '{"action":"new","expected_revision":1,"intent":"Add export capability","assessment":{}}',
            '{"action":"new","expected_revision":1,"intent":"Add export capability","assessment":{"operation":"NEW"}}',
            '{"action":"new","expected_revision":1,"intent":"Add export capability","assessment":{"operation":"CHANGE","classification":"FEATURE","confidence":"HIGH","capability_state":"PLANNED","selected_capability":"Export capability","candidate_capabilities":[],"selected_feature_id":null,"evidence":[{"source":"agent","reference":"tests/test_agent_protocol.py"}],"material_ambiguities":[],"recommended_depth":{"review":"NORMAL","verification":"NORMAL","convergence":"NORMAL"},"rationale":"attributable protocol assessment"}}',
            '{"action":"new","expected_revision":1,"intent":"Add export capability","assessment":{"operation":"NEW","classification":"FEATURE","confidence":"HIGH","capability_state":"PLANNED","selected_capability":"Export capability","candidate_capabilities":{},"selected_feature_id":null,"evidence":[{"source":"agent","reference":"tests/test_agent_protocol.py"}],"material_ambiguities":[],"recommended_depth":{"review":"NORMAL","verification":"NORMAL","convergence":"NORMAL"},"rationale":"attributable protocol assessment"}}',
            '{"action":"new","expected_revision":1,"intent":"Add export capability","assessment":{"operation":"NEW","classification":"FEATURE","confidence":"HIGH","capability_state":"PLANNED","selected_capability":"Export capability","candidate_capabilities":[],"selected_feature_id":null,"evidence":[{"source":"agent","reference":"tests/test_agent_protocol.py"}],"material_ambiguities":[],"recommended_depth":{"review":"NORMAL","verification":"NORMAL","convergence":"NORMAL"},"rationale":"attributable protocol assessment","unexpected":true}}',
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialized = json.loads(execute(root, request("init", expected_revision=0)))
            self.assertEqual(initialized["result"]["revision"], 1)
            before = CoreService.open(root).recover()

            # When: every incomplete or invalid historical assessment is replayed verbatim
            responses = tuple(json.loads(execute(root, payload)) for payload in malformed_payloads)

            # Then: strict rejection leaves authority unchanged before the complete replacement
            self.assertTrue(all(response["error"]["code"] == "INVALID_REQUEST" for response in responses))
            self.assertEqual(CoreService.open(root).recover(), before)

            # When: the ninth, full typed intake replaces the malformed attempts
            complete = json.loads(execute(
                root,
                request("new", expected_revision=1, intent="Add export capability", assessment=assessment("NEW", "PLANNED", "FEATURE")),
            ))

            # Then: the complete request reaches the unchanged strict intake path
            self.assertEqual(CoreService.open(root).recover().revision, complete["result"]["revision"])
            self.assertGreater(complete["result"]["revision"], before.revision)
            self.assertEqual(complete["result"]["code"], "PROPOSED")
    def test_uninitialized_context_and_intake_are_normal_non_discovering_responses(self) -> None:
        # Given: an uninitialized root with valid typed intake payloads
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = CoreService.open(root).recover()

            # When: context, NEW, and CHANGE reach the protocol boundary
            with (
                patch("evospec.agent.WorkflowService.external_sdd_evidence") as external_discovery,
                patch("evospec.workflow.DiscoveryPolicy.capability_candidates") as capability_discovery,
            ):
                responses = tuple(
                    json.loads(execute(root, payload))
                    for payload in (
                        request("context"),
                        request("new", expected_revision=0, intent="Add export capability", assessment=assessment("NEW", "PLANNED", "FEATURE")),
                        request("change", expected_revision=0, intent="Change export capability", assessment=assessment("CHANGE", "PLANNED", "PATCH")),
                    )
                )

            # Then: each action exposes recovery, constrained context, and no mutation
            self.assertTrue(all(response["result"]["code"] == "INIT_REQUIRED" for response in responses))
            self.assertTrue(all(response["context"]["initialization_state"] == "UNINITIALIZED" for response in responses))
            self.assertTrue(all(response["context"]["legal_actions"] == ["init", "context"] for response in responses))
            self.assertEqual(responses[0]["context"]["slice_acceptance_inventory"], {
                "source": "CORE_AGREEMENTS",
                "total": 0,
                "complete": True,
                "truncated": False,
                "history_scope": "AGREEMENT_RECORDS_ONLY_NONEXHAUSTIVE",
                "items": [],
            })
            init_guidance = responses[0]["context"]["guidance"][1]
            self.assertEqual(
                init_guidance,
                {"action": "init", "allowed": True, "reason_code": None, "blocker": None,
                 "required_actions": [], "next_action": "init", "intervention": "USER",
                 "scope": [], "message": "repository authority can be initialized"},
            )
            external_discovery.assert_not_called()
            capability_discovery.assert_not_called()
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_provisional_slice_context_is_additive_read_only_and_distinguishes_empty(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            uninitialized = json.loads(execute(root, request("context")))["context"]
            self.assertEqual(uninitialized["provisional_slice_inventory"], {
                "source": "CORE_PROVISIONAL_SLICE_HEADS",
                "availability": "UNINITIALIZED",
                "total": 0,
                "complete": False,
                "truncated": False,
                "items": [],
            })
            self.assertFalse((root / ".evospec").exists())

            initialized = json.loads(execute(root, request("init", expected_revision=0)))
            state_path = root / ".evospec" / "state.json"
            before = (state_path.read_bytes(), state_path.stat().st_mtime_ns)
            state_before_context = CoreService.open(root).recover()
            first = json.loads(execute(root, request("context")))["context"]
            second = json.loads(execute(root, request("context")))["context"]
            self.assertEqual(initialized["context"]["revision"], first["revision"])
            self.assertEqual(first["provisional_slice_inventory"], {
                "source": "CORE_PROVISIONAL_SLICE_HEADS",
                "availability": "AVAILABLE",
                "total": 0,
                "complete": True,
                "truncated": False,
                "items": [],
            })
            self.assertEqual(first["provisional_slice_inventory"], second["provisional_slice_inventory"])
            self.assertEqual(CoreService.open(root).recover(), state_before_context)
            self.assertEqual(
                (state_path.read_bytes(), state_path.stat().st_mtime_ns), before,
            )

        from tests.test_persistence import (
            _ProvisionalSliceTestHarness,
            _create_provisional_fixture,
            _make_provisional_revision,
        )

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = _create_provisional_fixture(root)
            proposal = _make_provisional_revision(base, title="Protocol proposal")
            committed = _ProvisionalSliceTestHarness(root).publish(base, proposal, None)
            authority_root = root / ".evospec"

            def authority_snapshot() -> dict[str, tuple[bytes, int]]:
                return {
                    path.relative_to(authority_root).as_posix(): (
                        path.read_bytes(), path.stat().st_mtime_ns,
                    )
                    for path in authority_root.rglob("*")
                    if path.is_file()
                }

            before_files = authority_snapshot()
            first = json.loads(execute(root, request("context")))["context"]
            second = json.loads(execute(root, request("context")))["context"]
            projection = first["provisional_slice_inventory"]
            self.assertEqual(first["revision"], committed.revision)
            self.assertEqual(projection["availability"], "AVAILABLE")
            self.assertEqual(projection["total"], 1)
            self.assertEqual(set(projection["items"][0]), {
                "proposal_id", "head", "repository_id", "feature_id", "title",
                "status", "provenance", "criteria",
            })
            self.assertEqual(projection["items"][0]["proposal_id"], proposal.proposal_id)
            self.assertEqual(projection, second["provisional_slice_inventory"])
            self.assertEqual(CoreService.open(root).recover(), committed)
            self.assertEqual(authority_snapshot(), before_files)

    def test_provisional_slice_context_fails_closed_for_corrupt_and_cross_root_state(self) -> None:
        from tests.test_persistence import (
            _ProvisionalSliceTestHarness,
            _create_provisional_fixture,
            _make_provisional_revision,
        )

        with TemporaryDirectory() as source_temporary, TemporaryDirectory() as other_temporary:
            root = Path(source_temporary)
            other_root = Path(other_temporary)
            base = _create_provisional_fixture(root)
            proposal = _make_provisional_revision(base, title="Root-bound proposal")
            _ProvisionalSliceTestHarness(root).publish(base, proposal, None)
            shutil.copytree(root / ".evospec", other_root / ".evospec")
            copied_state = other_root / ".evospec" / "state.json"
            copied_before = copied_state.read_bytes()

            wrong_root = json.loads(execute(other_root, request("context")))

            self.assertEqual(wrong_root["error"]["code"], "WORKFLOW_ERROR")
            self.assertNotIn("context", wrong_root)
            self.assertEqual(copied_state.read_bytes(), copied_before)

            state_path = root / ".evospec" / "state.json"
            altered = json.loads(state_path.read_text(encoding="utf-8"))
            altered["provisional_slice_heads"][0]["digest"] = "0" * 64
            state_path.write_text(json.dumps(altered), encoding="utf-8")
            corrupted_before = state_path.read_bytes()

            corrupt_response = json.loads(execute(root, request("context")))

            self.assertEqual(corrupt_response["error"]["code"], "WORKFLOW_ERROR")
            self.assertNotIn("context", corrupt_response)
            self.assertEqual(state_path.read_bytes(), corrupted_before)

    def test_context_serializes_the_fact_derived_outcome_and_legacy_intervention(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)

            uninitialized = json.loads(execute(root, request("context")))
            self.assertEqual(uninitialized["context"]["outcome"], {
                "kind": "STRUCTURAL_DEFECT",
                "message": "repository authority initialization is required",
                "intervention": "NONE",
                "material_decision": None,
            })

            initialized = json.loads(execute(root, request("init", expected_revision=0)))
            automatic = initialized["context"]["outcome"]
            self.assertEqual(automatic["kind"], "SAFE_AUTOMATIC_OPERATION")
            self.assertEqual(automatic["intervention"], "AUTOMATIC")
            self.assertIsNone(automatic["material_decision"])

            workflow = WorkflowService.open(root)
            workflow.new("Export capability")
            workflow.record_progress("implement", "export", (), "write", (), "test")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("tests",), True))
            workflow.converge(convergence_assessment(workflow.status().change_id, True))
            terminal = json.loads(execute(root, request("context")))["context"]
            self.assertTrue(terminal["completed_terminal"])
            self.assertIsNone(terminal["outcome"])

    def test_json_decoder_value_and_recursion_errors_are_structured_invalid_requests(self) -> None:
        cases = (
            ("integer limit", '{"action":"context","integer":' + "1" * 5_000 + "}", None),
            ("nesting limit", request("context"), RecursionError("nesting limit")),
        )
        for name, payload, decoder_error in cases:
            with self.subTest(name=name), TemporaryDirectory() as temporary:
                # Given: a request decoder failure before workflow dispatch
                root = Path(temporary)
                before = CoreService.open(root).recover()

                # When: JSON raises a parser resource-limit failure
                if decoder_error is None:
                    response_text = execute(root, payload)
                else:
                    with patch("evospec.agent_protocol_schema.json.loads", side_effect=decoder_error):
                        response_text = execute(root, payload)
                response = json.loads(response_text)

                # Then: the failure is an invalid request and authority remains unchanged
                self.assertEqual(response["error"]["code"], "INVALID_REQUEST")
                self.assertEqual(CoreService.open(root).recover(), before)

    def test_corrupt_state_is_a_structured_workflow_error_without_mutation(self) -> None:
        # Given: persisted state that cannot be safely recovered
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / ".evospec" / "state.json"
            state_path.parent.mkdir()
            state_path.write_text("not json", encoding="utf-8")
            before = state_path.read_bytes()

            # When: an in-process protocol request loads the corrupt authority
            response = json.loads(execute(root, request("context")))

            # Then: recovery is structured and the persisted bytes are unchanged
            self.assertEqual(response["error"]["code"], "WORKFLOW_ERROR")
            self.assertEqual(state_path.read_bytes(), before)

    def test_schema_nine_invalid_work_queue_enum_is_a_structured_recovery_error(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Queued capability")
            workflow.park("deferred")
            state_path = root / ".evospec" / "state.json"
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            payload["work_items"][0]["operational_state"] = "INVALID"
            state_path.write_text(json.dumps(payload), encoding="utf-8")
            before = state_path.read_bytes()

            response = json.loads(execute(root, request("context")))

            self.assertEqual(response["error"]["code"], "WORKFLOW_ERROR")
            self.assertEqual(state_path.read_bytes(), before)

    def test_subprocess_corrupt_state_prints_structured_workflow_error_without_mutation(self) -> None:
        # Given: persisted state that cannot be safely recovered
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / ".evospec" / "state.json"
            state_path.parent.mkdir()
            state_path.write_text("not json", encoding="utf-8")
            before = state_path.read_bytes()

            # When: the subprocess protocol entry point loads the corrupt authority
            completed = subprocess.run(
                [sys.executable, "-m", "evospec.agent_protocol", "--root", str(root)],
                input=request("context"),
                capture_output=True,
                text=True,
                check=False,
            )

            # Then: it returns a successful structured recovery error without changing state
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(json.loads(completed.stdout)["error"]["code"], "WORKFLOW_ERROR")
            self.assertEqual(state_path.read_bytes(), before)

    def test_invalid_evidence_repository_path_is_structured_and_does_not_mutate(self) -> None:
        # Given: an empty temporary repository and a nonescaping-invalid evidence path
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = CoreService.open(root).recover()
            invalid_assessment = assessment("NEW", "PLANNED", "FEATURE")
            invalid_assessment["evidence"] = [{"source": "agent", "reference": "../outside"}]

            # When: the JSON protocol receives the invalid assessment
            response = json.loads(
                execute(root, request("new", expected_revision=0, intent="Add export capability", assessment=invalid_assessment))
            )

            # Then: parsing is an invalid request and authoritative state is unchanged
            self.assertEqual(response["error"]["code"], "INVALID_REQUEST")
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_subprocess_invalid_evidence_repository_path_prints_structured_successful_error(self) -> None:
        # Given: an empty temporary root and a nonescaping-invalid evidence path
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = CoreService.open(root).recover()
            invalid_assessment = assessment("NEW", "PLANNED", "FEATURE")
            invalid_assessment["evidence"] = [{"source": "agent", "reference": "../outside"}]
            payload = request("new", expected_revision=0, intent="Add export capability", assessment=invalid_assessment)

            # When: the actual subprocess entry point receives the request
            completed = subprocess.run(
                [sys.executable, "-m", "evospec.agent_protocol", "--root", str(root)],
                input=payload,
                capture_output=True,
                text=True,
                check=False,
            )

            # Then: the expected client error is structured, successful, and non-mutating
            self.assertEqual(completed.returncode, 0)
            self.assertIn('"code":"INVALID_REQUEST"', completed.stdout)
            self.assertEqual(json.loads(completed.stdout)["error"]["code"], "INVALID_REQUEST")
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_subprocess_uses_strict_utf8_for_accented_and_cjk_requests(self) -> None:
        # Given: a byte payload containing accented and CJK text
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            intent = "Ajouter l'export café - 漢字"
            payload = request("init", expected_revision=0).encode("utf-8")

            # When: the protocol subprocess receives UTF-8 bytes and returns UTF-8 bytes
            initialized = subprocess.run(
                [sys.executable, "-m", "evospec.agent_protocol", "--root", str(root)],
                input=payload,
                capture_output=True,
                check=False,
            )
            created_payload = request(
                "new",
                expected_revision=json.loads(initialized.stdout.decode("utf-8"))["result"]["revision"],
                intent=intent,
                assessment=assessment("NEW", "PLANNED", "FEATURE"),
            ).encode("utf-8")
            self.assertIn("café".encode("utf-8"), created_payload)
            self.assertIn("漢字".encode("utf-8"), created_payload)
            created = subprocess.run(
                [sys.executable, "-m", "evospec.agent_protocol", "--root", str(root)],
                input=created_payload,
                capture_output=True,
                check=False,
            )

            # Then: the exact text survives the byte boundary without locale conversion
            self.assertEqual(created.returncode, 0)
            self.assertEqual(CoreService.open(root).recover().changes[0].intent, intent)

    def test_subprocess_rejects_malformed_utf8_as_a_structured_request_error(self) -> None:
        # Given: malformed bytes after an otherwise valid request
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = CoreService.open(root).recover()

            # When: the real process decodes invalid UTF-8
            completed = subprocess.run(
                [sys.executable, "-m", "evospec.agent_protocol", "--root", str(root)],
                input=b'{"action":"context"}\xff',
                capture_output=True,
                check=False,
            )

            # Then: it reports a successful structured rejection and preserves state
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(json.loads(completed.stdout.decode("utf-8"))["error"]["code"], "INVALID_REQUEST")
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_unknown_contract_keys_are_structured_and_non_mutating(self) -> None:
        # Given: every action root and nested contract has an unexpected key
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = CoreService.open(root).recover()
            valid_assessment = assessment("NEW", "PLANNED", "FEATURE")
            valid_lock: dict[str, JsonValue] = {"lock_id": "lock-export", "lock_revision": 1, "affected_aspects": [], "protected_touches": [], "preserving_path": "preserve", "conflict": False, "evasion": False, "confidence": "HIGH", "evidence": [], "rationale": "unchanged"}
            valid_convergence: dict[str, JsonValue] = {"change_id": "change-export", "product_truth_changed": True, "product_truth_content": "# Export\n", "confidence": "HIGH", "evidence": [{"source": "agent", "reference": "tests/test_agent_protocol.py"}], "rationale": "explicit"}
            valid_progress: dict[str, JsonValue] = {"expected_revision": 0, "current_action": "implement", "focus": "export", "completed_work": [], "current_work": "write", "pending_work": [], "resume_point": "test"}
            valid_review: dict[str, JsonValue] = {"verdict": "PASS", "findings": [], "safe_to_proceed": True}
            valid_verification: dict[str, JsonValue] = {"evidence": [], "sufficient": True}
            unexpected_depth: dict[str, JsonValue] = {"review": "NORMAL", "verification": "NORMAL", "convergence": "NORMAL", "unexpected": True}
            unexpected_evidence: list[JsonValue] = [{"source": "agent", "reference": "tests/test_agent_protocol.py", "unexpected": True}]
            unexpected_lock: dict[str, JsonValue] = dict(valid_lock)
            unexpected_lock["unexpected"] = True
            unexpected_review: dict[str, JsonValue] = {"verdict": "PASS", "findings": [], "safe_to_proceed": True, "unexpected": True}
            unexpected_assessment: dict[str, JsonValue] = dict(valid_assessment)
            unexpected_assessment["unexpected"] = True
            unexpected_depth_assessment: dict[str, JsonValue] = dict(valid_assessment)
            unexpected_depth_assessment["recommended_depth"] = unexpected_depth
            unexpected_evidence_assessment: dict[str, JsonValue] = dict(valid_assessment)
            unexpected_evidence_assessment["evidence"] = unexpected_evidence
            unexpected_convergence: dict[str, JsonValue] = dict(valid_convergence)
            unexpected_convergence["unexpected"] = True
            requests: tuple[str, ...] = (
                request("context", unexpected=True),
                request("init", expected_revision=0, unexpected=True),
                request("new", expected_revision=0, intent="Export", assessment=valid_assessment, unexpected=True),
                request("change", expected_revision=0, intent="Export", assessment=assessment("CHANGE", "PLANNED", "PATCH"), unexpected=True),
                request("progress", **{**valid_progress, "unexpected": True}),
                request("progress", **{**valid_progress, "resume_pont": "typo"}),
                request("assess_locks", expected_revision=0, assessments=[], unexpected=True),
                request("review", expected_revision=0, review=valid_review, unexpected=True),
                request("verify", expected_revision=0, verification=valid_verification, unexpected=True),
                request("converge", expected_revision=0, assessment=valid_convergence, unexpected=True),
                request("invalidate_assurance", expected_revision=0, unexpected=True),
                request("park", expected_revision=0, unexpected=True),
                request("resume", expected_revision=0, unexpected=True),
                request("new", expected_revision=0, intent="Export", assessment=unexpected_depth_assessment),
                request("new", expected_revision=0, intent="Export", assessment=unexpected_evidence_assessment),
                request("assess_locks", expected_revision=0, assessments=[unexpected_lock]),
                request("review", expected_revision=0, review=unexpected_review),
                request("verify", expected_revision=0, verification={"evidence": [], "sufficient": True, "unexpected": True}),
                request("converge", expected_revision=0, assessment=unexpected_convergence),
            )

            # When: each request is decoded before it can reach Core mutation
            responses = tuple(json.loads(execute(root, payload)) for payload in requests)

            # Then: every surplus key is rejected and no state changes
            self.assertTrue(all(response["error"]["code"] == "INVALID_REQUEST" for response in responses))
            self.assertIn("resume_pont", responses[5]["error"]["detail"])
            self.assertEqual(CoreService.open(root).recover(), before)
    def test_protocol_routes_valid_mutations_through_real_workflow_service(self) -> None:
        # Given: an empty temporary repository and typed protocol input
        with TemporaryDirectory() as temporary:
            root = Path(temporary)

            # When: the full false-convergence lifecycle is requested through the protocol
            initialized = json.loads(execute(root, request("init", expected_revision=0)))
            intake = json.loads(execute(root, request("new", expected_revision=initialized["result"]["revision"], intent="Add export capability", assessment=assessment("NEW", "PLANNED", "FEATURE"))))
            progressed = json.loads(execute(root, request("progress", expected_revision=intake["result"]["revision"], current_action="implement", focus="export", completed_work=[], current_work="write", pending_work=["test"], resume_point="run tests")))
            reviewed = json.loads(execute(root, request("review", expected_revision=progressed["result"]["revision"], review={"verdict": "PASS", "findings": [], "safe_to_proceed": True})))
            verified = json.loads(execute(root, request("verify", expected_revision=reviewed["result"]["revision"], verification={"evidence": ["tests"], "sufficient": True})))
            invalidated = json.loads(execute(root, request("invalidate_assurance", expected_revision=verified["result"]["revision"])))
            stale_invalidation = json.loads(execute(root, request("invalidate_assurance", expected_revision=verified["result"]["revision"])))
            change_id = invalidated["result"]["change_id"]
            if type(change_id) is not str:
                self.fail("expected active change")
            fresh_progress = json.loads(execute(root, request("progress", expected_revision=invalidated["result"]["revision"], current_action="implement", focus="export", completed_work=["write"], current_work="refresh assurance", pending_work=["test"], resume_point="review again")))
            rereviewed = json.loads(execute(root, request("review", expected_revision=fresh_progress["result"]["revision"], review={"verdict": "PASS", "findings": [], "safe_to_proceed": True})))
            reverified = json.loads(execute(root, request("verify", expected_revision=rereviewed["result"]["revision"], verification={"evidence": ["tests"], "sufficient": True})))
            converged = json.loads(execute(root, request("converge", expected_revision=reverified["result"]["revision"], assessment={"change_id": change_id, "product_truth_changed": False, "confidence": "HIGH", "evidence": [{"source": "agent", "reference": "tests/test_agent_protocol.py"}], "rationale": "documentation-only result"})))

            # Then: every mutation reports the facade outcome and the archive has no publication
            self.assertEqual(initialized["result"]["code"], "INITIALIZED")
            self.assertEqual(intake["result"]["code"], "PROPOSED")
            self.assertEqual(progressed["result"]["code"], "IN_PROGRESS")
            self.assertEqual(progressed["result"]["message"], "implementation progress persisted")
            self.assertEqual(progressed["result"]["pending_work"], ["test"])
            self.assertEqual(progressed["result"]["verification_sufficient"], False)
            self.assertEqual(progressed["result"]["review_depth"], "NORMAL")
            self.assertEqual(reviewed["result"]["code"], "REVIEWED")
            self.assertEqual(reviewed["context"]["legal_actions"], ["context", "list_work", "next_work", "park", "assess_locks", "review", "invalidate_assurance", "reject", "verify", "authorize_irreversible", "propose_autonomy_expansion"])
            self.assertEqual(verified["result"]["code"], "VERIFIED")
            self.assertIn("converge", verified["context"]["legal_actions"])
            self.assertEqual(invalidated["result"]["code"], "ASSURANCE_INVALIDATED")
            self.assertEqual(invalidated["context"]["lifecycle"], ["IN_PROGRESS", "IMPLEMENT"])
            self.assertIsNone(invalidated["context"]["decision_state"]["review_verdict"])
            self.assertFalse(invalidated["context"]["decision_state"]["verification_sufficient"])
            self.assertEqual(stale_invalidation["error"]["code"], "STALE_REVISION")
            self.assertEqual(fresh_progress["result"]["code"], "IN_PROGRESS")
            self.assertEqual(converged["result"]["code"], "DONE")
            self.assertEqual(WorkflowService.open(root).status().code, "STATUS")
            self.assertEqual(CoreService.open(root).recover().publications, ())

    def test_recovered_context_question_resolves_ambiguous_work_at_its_revision(self) -> None:
        # Given: a persisted material ambiguity created through the strict protocol
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialized = json.loads(execute(root, request("init", expected_revision=0)))
            ambiguous_assessment = assessment("NEW", "PLANNED", "FEATURE")
            ambiguous_assessment["material_ambiguities"] = [{"decision": "delivery channel", "options": ["email", "download"]}]
            intake = json.loads(execute(root, request("new", expected_revision=initialized["result"]["revision"], intent="Add export delivery", assessment=ambiguous_assessment)))

            # When: a fresh context response supplies one returned question ID
            recovered = json.loads(execute(root, request("context")))
            questions = recovered["context"]["questions"]
            question_id = questions[0]["id"]
            if type(question_id) is not str:
                self.fail("expected returned clarification question ID")
            clarified = json.loads(execute(root, request("clarify", expected_revision=recovered["context"]["revision"], answers=[{"question_id": question_id, "answer": "email"}])))

            # Then: context preserves the normal question pair and strict clarification reaches READY
            self.assertEqual(questions, intake["result"]["questions"])
            self.assertEqual(clarified["result"]["code"], "READY")
            self.assertEqual(clarified["context"]["lifecycle"], ["READY", "READY"])
            self.assertNotIn("questions", clarified["context"])

    def test_legacy_recovery_protocol_rejects_wrong_identity_and_projects_exact_choices(self) -> None:
        from dataclasses import replace
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Add export")
            core = CoreService.open(root)
            state = core.recover()
            active = state.active_context
            assert active is not None and active.change_id is not None and state.initialization is not None
            initialization = state.initialization
            state = core.record_safety_facts(state, True, replace(active.safety_facts, unresolved_decision_details=("Choose delivery",)))
            fields: dict[str, JsonValue] = dict(expected_revision=state.revision, repository_id=initialization.repository_id,
                                                feature_id=str(active.feature_id), change_id=str(active.change_id),
                                                legacy_detail="Choose delivery", decision="Select delivery", options=["email", "download"])
            self.assertEqual(json.loads(execute(root, request("context")))["context"]["outcome"]["kind"], "STRUCTURAL_DEFECT")
            self.assertIn("structure_material_clarification", json.loads(execute(root, request("context")))["context"]["legal_actions"])
            self.assertEqual(json.loads(execute(root, request("context")))["context"]["repository_id"], initialization.repository_id)
            for wrong in (dict(fields, feature_id="wrong"), dict(fields, change_id="wrong"),
                          dict(fields, legacy_detail="wrong"), dict(fields, repository_id="wrong")):
                self.assertEqual(json.loads(execute(root, request("structure_material_clarification", **wrong)))["error"]["code"], "WORKFLOW_ERROR")
            invalid_options: tuple[JsonValue, ...] = (["only"], ["same", "same"], ["email", ""], "not-an-array")
            for options in invalid_options:
                self.assertIn("error", json.loads(execute(root, request("structure_material_clarification", **dict(fields, options=options)))))
            self.assertEqual(core.recover().revision, state.revision)
            repaired = json.loads(execute(root, request("structure_material_clarification", **fields)))
            self.assertEqual(repaired["context"]["outcome"]["kind"], "HUMAN_MATERIAL_DECISION")
            self.assertEqual(repaired["context"]["outcome"]["material_decision"], {"decision": "Select delivery", "options": ["email", "download"]})
            self.assertEqual(repaired["context"]["decision_state"]["unresolved_material_decision"], True)
            self.assertEqual(repaired["context"]["autonomy_metrics"]["human_material_decisions"], 0)
            self.assertEqual(json.loads(execute(root, request("structure_material_clarification", **fields)))["error"]["code"], "STALE_REVISION")
            self.assertEqual(json.loads(execute(root, request("structure_material_clarification", **dict(fields, expected_revision=repaired["context"]["revision"]))))["error"]["code"], "WORKFLOW_ERROR")

    def test_legacy_non_material_recovery_protocol_round_trip_is_strict_and_auditable(self) -> None:
        from dataclasses import replace
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Record an operational legacy note")
            core = CoreService.open(root)
            state = core.recover()
            active = state.active_context
            initialization = state.initialization
            assert active is not None and active.change_id is not None and initialization is not None
            detail = "Inspect the current routing table before changing behavior."
            state = core.record_safety_facts(
                state,
                True,
                replace(active.safety_facts, unresolved_decision_details=(detail,)),
            )
            before = json.loads(execute(root, request("context")))["context"]
            self.assertIn("recover_legacy_non_material_detail", before["legal_actions"])
            fields: dict[str, JsonValue] = dict(
                expected_revision=state.revision,
                repository_id=initialization.repository_id,
                feature_id=str(active.feature_id),
                change_id=str(active.change_id),
                legacy_detail=detail,
            )
            malformed = json.loads(
                execute(root, request("recover_legacy_non_material_detail", **dict(fields, replacement_prose="rephrased")))
            )
            self.assertEqual(malformed["error"]["code"], "INVALID_REQUEST")
            self.assertEqual(core.recover().revision, state.revision)
            blank = json.loads(
                execute(root, request("recover_legacy_non_material_detail", **dict(fields, legacy_detail=" ")))
            )
            self.assertEqual(blank["error"]["code"], "INVALID_REQUEST")

            recovered = json.loads(
                execute(root, request("recover_legacy_non_material_detail", **fields))
            )
            self.assertEqual(recovered["result"]["code"], "READY")
            self.assertEqual(recovered["context"]["outcome"]["kind"], "SAFE_AUTOMATIC_OPERATION")
            self.assertFalse(recovered["context"]["decision_state"]["unresolved_material_decision"])
            self.assertEqual(
                recovered["context"]["autonomy_metrics"], before["autonomy_metrics"]
            )
            self.assertEqual(recovered["context"]["autonomy_envelope"], before["autonomy_envelope"])
            saved = CoreService.open(root).recover()
            self.assertEqual(saved.decisions, state.decisions)
            self.assertEqual(len(saved.observations), 1)
            self.assertEqual(saved.observations[0].kind, "legacy_non_material_recovery")
            self.assertEqual(saved.observations[0].statement, detail)
            self.assertFalse(saved.observations[0].accepted_into_truth)
            self.assertIsNone(saved.observations[0].outcome)

    def test_inactive_recovery_exposes_accepted_capability_for_established_change(self) -> None:
        # Given: true protocol convergence that leaves only accepted Product Truth
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialized = json.loads(execute(root, request("init", expected_revision=0)))
            intake = json.loads(execute(root, request("new", expected_revision=initialized["result"]["revision"], intent="Export capability", assessment=assessment("NEW", "PLANNED", "FEATURE"))))
            progressed = json.loads(execute(root, request("progress", expected_revision=intake["result"]["revision"], current_action="implement", focus="export", completed_work=[], current_work="write", pending_work=[], resume_point="test")))
            reviewed = json.loads(execute(root, request("review", expected_revision=progressed["result"]["revision"], review={"verdict": "PASS", "findings": [], "safe_to_proceed": True})))
            verified = json.loads(execute(root, request("verify", expected_revision=reviewed["result"]["revision"], verification={"evidence": ["tests"], "sufficient": True})))
            change_id = verified["result"]["change_id"]
            if type(change_id) is not str:
                self.fail("expected active change")
            converged = json.loads(execute(root, request("converge", expected_revision=verified["result"]["revision"], assessment={"change_id": change_id, "product_truth_changed": True, "product_truth_content": "# Export capability\n\nAccepted Product Truth.\n", "confidence": "HIGH", "evidence": [{"source": "agent", "reference": "tests/test_agent_protocol.py"}], "rationale": "accepted behavior changed"})))

            # When: a recovered agent uses the returned accepted Feature identity in CHANGE
            recovered = json.loads(execute(root, request("context")))
            capabilities = recovered["context"]["accepted_capabilities"]
            feature_id = capabilities[0]["feature_id"]
            if type(feature_id) is not str:
                self.fail("expected accepted feature identity")
            changed = json.loads(execute(root, request("change", expected_revision=recovered["context"]["revision"], intent="Change export capability", assessment=assessment("CHANGE", "ESTABLISHED", "PATCH", feature_id))))

            # Then: context is complete and Core accepts exactly the recovered Feature identity
            self.assertEqual(converged["result"]["code"], "DONE")
            self.assertTrue(recovered["context"]["accepted_capabilities_complete"])
            self.assertEqual(capabilities[0]["title"], "Export capability")
            self.assertEqual(changed["result"]["feature_id"], feature_id)

    def test_protocol_returns_normal_guidance_for_premature_convergence_without_mutation(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialized = json.loads(execute(root, request("init", expected_revision=0)))
            intake = json.loads(execute(root, request("new", expected_revision=initialized["result"]["revision"], intent="Add export capability", assessment=assessment("NEW", "PLANNED", "FEATURE"))))
            change_id = intake["result"]["change_id"]
            if type(change_id) is not str:
                self.fail("expected active change")

            response = json.loads(execute(root, request("converge", expected_revision=intake["result"]["revision"], assessment={"change_id": change_id, "product_truth_changed": False, "confidence": "HIGH", "evidence": [{"source": "agent", "reference": "tests/test_agent_protocol.py"}], "rationale": "premature"})))

            self.assertEqual(response["result"]["code"], "REVIEW_REQUIRED")
            convergence = next(
                item for item in response["context"]["guidance"]
                if item["action"] == "converge"
            )
            self.assertEqual(convergence["reason_code"], "REVIEW_REQUIRED")
            self.assertEqual(CoreService.open(root).recover().revision, intake["result"]["revision"])

    def test_protocol_preserves_lock_assessment_and_rejects_stale_revision(self) -> None:
        # Given: accepted truth, a current semantic lock, and a later assessed change
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            baseline = workflow.new("Export capability")
            workflow.record_progress("implement", "export", (), "write", (), "test")
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("tests",), True))
            workflow.converge(convergence_assessment(workflow.status().change_id, True))
            if baseline.feature_id is None:
                self.fail("expected baseline feature")
            state = CoreService.open(root).recover()
            workflow.create_lock(
                SemanticLock(LockId("lock-export"), FeatureId(baseline.feature_id), 1),
                SemanticLockRevision(LockId("lock-export"), 1, state.publications[0], ("delivery",), "preserve delivery"),
            )
            workflow.create_lock(
                SemanticLock(LockId("lock-retention"), FeatureId(baseline.feature_id), 1),
                SemanticLockRevision(LockId("lock-retention"), 1, state.publications[0], ("retention",), "preserve retention"),
            )
            changed = json.loads(execute(root, request("change", expected_revision=workflow.status().revision, intent="Change export capability", assessment=assessment("CHANGE", "ESTABLISHED", "PATCH", baseline.feature_id))))
            changed_id = changed["result"]["change_id"]
            if type(changed_id) is not str:
                self.fail("expected changed identity")
            workflow.record_progress("implement", "export", (), "write", (), "test")

            # When: the adapter supplies complete coverage with one stale revision
            expected_revision = workflow.status().revision
            valid = json.loads(execute(root, request("assess_locks", expected_revision=expected_revision, assessments=[{"lock_id": "lock-retention", "lock_revision": 1, "affected_aspects": ["retention"], "protected_touches": [], "preserving_path": "preserve retention", "conflict": False, "evasion": False, "confidence": "HIGH", "evidence": ["tests"], "rationale": "no protected change"}, {"lock_id": "lock-export", "lock_revision": 1, "affected_aspects": ["delivery"], "protected_touches": [], "preserving_path": "preserve delivery", "conflict": False, "evasion": False, "confidence": "HIGH", "evidence": ["tests"], "rationale": "no protected change"}])))
            after_valid = CoreService.open(root).recover()
            stale = json.loads(execute(root, request("assess_locks", expected_revision=expected_revision, assessments=[{"lock_id": "lock-retention", "lock_revision": 1, "affected_aspects": ["retention"], "protected_touches": [], "preserving_path": "preserve retention", "conflict": False, "evasion": False, "confidence": "HIGH", "evidence": ["tests"], "rationale": "no protected change"}, {"lock_id": "lock-export", "lock_revision": 1, "affected_aspects": ["delivery"], "protected_touches": [], "preserving_path": "preserve delivery", "conflict": False, "evasion": False, "confidence": "HIGH", "evidence": ["tests"], "rationale": "stale state submission"}])))

            # Then: Core accepts the current assessment and rejects the stale one without bypass
            self.assertEqual(valid["result"]["code"], "LOCKS_ASSESSED")
            self.assertFalse(valid["context"]["stale"])
            self.assertEqual(stale["error"]["code"], "STALE_REVISION")
            self.assertEqual(CoreService.open(root).recover(), after_valid)

    def test_stale_progress_rejects_before_any_partial_mutation(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Export capability")
            expected_revision = workflow.status().revision
            workflow.record_progress("intervening", "authority", (), "preserved", (), "resume")
            intervening = CoreService.open(root).recover()

            response = json.loads(execute(root, request("progress", expected_revision=expected_revision, current_action="stale", focus="stale", completed_work=[], current_work="overwrite", pending_work=[], resume_point="stale")))

            self.assertEqual(response["error"]["code"], "STALE_REVISION")
            self.assertEqual(response["error"]["expected_revision"], expected_revision)
            self.assertEqual(response["error"]["actual_revision"], intervening.revision)
            self.assertEqual(CoreService.open(root).recover(), intervening)

    def test_malformed_review_and_verification_are_structured_and_non_mutating(self) -> None:
        # Given: an implementation-ready protocol lifecycle
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialized = json.loads(execute(root, request("init", expected_revision=0)))
            intake = json.loads(execute(root, request("new", expected_revision=initialized["result"]["revision"], intent="Export capability", assessment=assessment("NEW", "PLANNED", "FEATURE"))))
            progressed = json.loads(execute(root, request("progress", expected_revision=intake["result"]["revision"], current_action="implement", focus="export", completed_work=[], current_work="write", pending_work=[], resume_point="test")))
            before_review = CoreService.open(root).recover()

            # When: malformed typed evidence is submitted at each protocol boundary
            malformed_review = json.loads(execute(root, request("review", expected_revision=progressed["result"]["revision"], review={"verdict": "UNKNOWN", "findings": [], "safe_to_proceed": True})))
            self.assertEqual(malformed_review["error"]["code"], "INVALID_REQUEST")
            self.assertEqual(before_review, CoreService.open(root).recover())
            reviewed = json.loads(execute(root, request("review", expected_revision=progressed["result"]["revision"], review={"verdict": "PASS", "findings": [], "safe_to_proceed": True})))
            before_verification = CoreService.open(root).recover()
            malformed_verification = json.loads(execute(root, request("verify", expected_revision=reviewed["result"]["revision"], verification={"evidence": ["tests"], "sufficient": "true"})))

            # Then: parse failures expose structured errors and preserve Core state
            self.assertEqual(malformed_verification["error"]["code"], "INVALID_REQUEST")
            self.assertEqual(before_verification, CoreService.open(root).recover())

    def test_true_convergence_publishes_only_explicit_content_once(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialized = json.loads(execute(root, request("init", expected_revision=0)))
            intake = json.loads(execute(root, request("new", expected_revision=initialized["result"]["revision"], intent="Export capability", assessment=assessment("NEW", "PLANNED", "FEATURE"))))
            progressed = json.loads(execute(root, request("progress", expected_revision=intake["result"]["revision"], current_action="implement", focus="export", completed_work=[], current_work="placeholder must not publish", pending_work=[], resume_point="test")))
            reviewed = json.loads(execute(root, request("review", expected_revision=progressed["result"]["revision"], review={"verdict": "PASS", "findings": [], "safe_to_proceed": True})))
            verified = json.loads(execute(root, request("verify", expected_revision=reviewed["result"]["revision"], verification={"evidence": ["tests"], "sufficient": True})))
            change_id = verified["result"]["change_id"]
            if type(change_id) is not str:
                self.fail("expected active change")
            content = "# Export capability\n\nCaller-authored Product Truth.\n"
            payload = request("converge", expected_revision=verified["result"]["revision"], assessment={"change_id": change_id, "product_truth_changed": True, "product_truth_content": content, "confidence": "HIGH", "evidence": [{"source": "agent", "reference": "tests/test_agent_protocol.py"}], "rationale": "accepted behavior changed"})

            converged = json.loads(execute(root, payload))
            accepted = CoreService.open(root).recover()
            replayed = json.loads(execute(root, payload))

            self.assertEqual(converged["result"]["code"], "DONE")
            self.assertEqual(len(accepted.publication_history), 1)
            self.assertEqual((root / ".evospec" / accepted.publications[0].path.value).read_text(encoding="utf-8"), content)
            self.assertEqual(replayed["error"]["code"], "STALE_REVISION")
            self.assertEqual(CoreService.open(root).recover(), accepted)

    def test_every_mutating_action_requires_expected_revision(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = CoreService.open(root).recover()
            convergence: dict[str, JsonValue] = {
                "change_id": "change-export",
                "product_truth_changed": False,
                "confidence": "HIGH",
                "evidence": [
                    {
                        "source": "agent",
                        "reference": "tests/test_agent_protocol.py",
                    }
                ],
                "rationale": "explicit false assessment",
            }
            requests = (
                request("init"),
                request(
                    "new",
                    intent="Add export capability",
                    assessment=assessment("NEW", "PLANNED", "FEATURE"),
                ),
                request(
                    "change",
                    intent="Change export capability",
                    assessment=assessment("CHANGE", "ESTABLISHED", "PATCH"),
                ),
                request(
                    "progress",
                    current_action="implement",
                    focus="export",
                    completed_work=[],
                    current_work="write",
                    pending_work=[],
                    resume_point="test",
                ),
                request("assess_locks", assessments=[]),
                request("review", review={"verdict": "PASS", "findings": [], "safe_to_proceed": True}),
                request("verify", verification={"evidence": ["tests"], "sufficient": True}),
                request("converge", assessment=convergence),
                request("invalidate_assurance"),
                request("clarify", answers=[]),
                request("continue"),
                request("park"),
                request("resume"),
            )

            responses = tuple(
                json.loads(execute(root, payload)) for payload in requests
            )

            self.assertTrue(all(item["error"]["code"] == "INVALID_REQUEST" for item in responses))
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_protocol_parks_and_resumes_only_with_the_current_revision(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialized = json.loads(execute(root, request("init", expected_revision=0)))
            intake = json.loads(execute(root, request("new", expected_revision=initialized["result"]["revision"], intent="Parkable capability", assessment=assessment("NEW", "PLANNED", "FEATURE"))))
            progressed = json.loads(execute(root, request("progress", expected_revision=intake["result"]["revision"], current_action="implement", focus="parkable", completed_work=[], current_work="verify", pending_work=[], resume_point="resume")))
            reviewed = json.loads(execute(root, request("review", expected_revision=progressed["result"]["revision"], review={"verdict": "PASS", "findings": [], "safe_to_proceed": True})))
            verified = json.loads(execute(root, request("verify", expected_revision=reviewed["result"]["revision"], verification={"evidence": ["blocked runner"], "sufficient": False})))
            expected_revision = verified["result"]["revision"]

            parked = json.loads(execute(root, request("park", expected_revision=expected_revision, reason="verification environment unavailable")))
            stale_park = json.loads(execute(root, request("park", expected_revision=expected_revision, reason="retry later")))
            missing_reason = json.loads(execute(root, request("park", expected_revision=parked["result"]["revision"])))
            blank_reason = json.loads(execute(root, request("park", expected_revision=parked["result"]["revision"], reason=" ")))
            mismatch = json.loads(execute(root, request("resume", expected_revision=parked["result"]["revision"], change_id="change-other")))
            work_id = parked["context"]["work_queue"]["frozen"][0]["id"]
            resumed = json.loads(execute(root, request("resume", expected_revision=parked["result"]["revision"], change_id=work_id)))

            self.assertEqual(parked["result"]["code"], "PARKED")
            self.assertIsNone(parked["context"]["lifecycle"])
            self.assertEqual(parked["context"]["work_queue"]["frozen"][0]["reason"], "verification environment unavailable")
            self.assertIn("resume", parked["context"]["legal_actions"])
            self.assertEqual(stale_park["error"]["code"], "STALE_REVISION")
            self.assertEqual(missing_reason["error"]["code"], "INVALID_REQUEST")
            self.assertEqual(blank_reason["error"]["code"], "INVALID_REQUEST")
            self.assertEqual(mismatch["error"]["code"], "WORKFLOW_ERROR")
            self.assertEqual(resumed["result"]["code"], "RESUMED")
            self.assertEqual(resumed["context"]["lifecycle"], ["VERIFYING", "VERIFY"])

    def test_protocol_projects_and_mutates_queue_only_at_current_revision(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialized = json.loads(execute(root, request("init", expected_revision=0)))
            first = json.loads(execute(root, request("new", expected_revision=initialized["result"]["revision"], intent="First queued capability", assessment=assessment("NEW", "PLANNED", "FEATURE"))))
            parked_first = json.loads(execute(root, request("park", expected_revision=first["result"]["revision"], reason="deferred")))
            second_assessment = assessment("NEW", "PLANNED", "FEATURE")
            second_assessment["selected_capability"] = "Second queued capability"
            second = json.loads(execute(root, request("new", expected_revision=parked_first["result"]["revision"], intent="Second queued capability", assessment=second_assessment)))
            parked_second = json.loads(execute(root, request("park", expected_revision=second["result"]["revision"], reason="external dependency")))

            queue = parked_second["context"]["work_queue"]
            first_id, second_id = (item["id"] for item in queue["pending"])
            listed = json.loads(execute(root, request("list_work", expected_revision=parked_second["result"]["revision"], filter="pending")))
            invalid = json.loads(execute(root, request("set_work_state", expected_revision=parked_second["result"]["revision"], work_id=first_id, operational_state="INVALID", reason=None)))
            ready = json.loads(execute(root, request("set_work_state", expected_revision=parked_second["result"]["revision"], work_id=first_id, operational_state="READY", reason=None)))
            stale = json.loads(execute(root, request("next_work", expected_revision=parked_second["result"]["revision"])))
            added = json.loads(execute(root, request("add_work_relation", expected_revision=ready["result"]["revision"], predecessor_id=first_id, successor_id=second_id, kind="HARD")))
            removed = json.loads(execute(root, request("remove_work_relation", expected_revision=added["result"]["revision"], predecessor_id=first_id, successor_id=second_id, kind="HARD")))
            activated = json.loads(execute(root, request("activate_work", expected_revision=removed["result"]["revision"], work_id=first_id)))

            self.assertEqual(len(listed["context"]["work_queue"]["pending"]), 2)
            self.assertEqual(listed["context"]["work_queue"]["next_candidates_total"], 0)
            self.assertTrue(listed["context"]["work_queue"]["next_candidates_complete"])
            self.assertEqual(invalid["error"]["code"], "INVALID_REQUEST")
            self.assertEqual(stale["error"]["code"], "STALE_REVISION")
            self.assertEqual(added["result"]["code"], "WORK_RELATION_ADDED")
            self.assertEqual(removed["result"]["code"], "WORK_RELATION_REMOVED")
            self.assertEqual(activated["result"]["code"], "ACTIVATED")
            self.assertEqual(activated["context"]["work_queue"]["active"]["id"], first_id)

    def test_queue_atomic_failures_are_structured_workflow_errors_without_revision_change(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("First queued capability")
            workflow.park("deferred")
            state = CoreService.open(root).recover()
            first = state.work_items[0]

            def assert_failure(payload: str, before) -> None:
                failing = WorkflowService(root, CoreService.open(root, FailingReplace()))
                with patch("evospec.agent_protocol.WorkflowService.open", return_value=failing):
                    response = json.loads(execute(root, payload))
                self.assertEqual(response["error"]["code"], "WORKFLOW_ERROR")
                self.assertEqual(CoreService.open(root).recover(), before)

            assert_failure(
                request("set_work_state", expected_revision=state.revision, work_id=str(first.id), operational_state="READY", reason=None),
                state,
            )
            state = CoreService.open(root).set_work_state(
                state, first.id, OperationalState.READY
            )
            assert_failure(
                request("activate_work", expected_revision=state.revision, work_id=str(first.id)),
                state,
            )

            workflow.new("Second queued capability")
            workflow.park("deferred")
            state = CoreService.open(root).recover()
            second = next(item for item in state.work_items if item.id != first.id)
            assert_failure(
                request("add_work_relation", expected_revision=state.revision, predecessor_id=str(first.id), successor_id=str(second.id), kind="HARD"),
                state,
            )
            state = CoreService.open(root).add_work_relation(
                state, WorkRelation(first.id, second.id, WorkRelationKind.HARD)
            )
            assert_failure(
                request("remove_work_relation", expected_revision=state.revision, predecessor_id=str(first.id), successor_id=str(second.id), kind="HARD"),
                state,
            )

    def test_protocol_returns_structured_side_work_and_invalid_request_errors(self) -> None:
        # Given: a repository in active side work
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Primary capability")
            workflow.record_progress("implement", "primary", (), "work", (), "resume")
            activate_side(root)

            # When: context and malformed protocol input are submitted
            context = json.loads(execute(root, request("context")))
            invalid = json.loads(execute(root, "{not json"))
            duplicate = json.loads(execute(root, '{"action":"progress","action":"progress","expected_revision":0}'))
            non_finite = json.loads(
                execute(root, '{"action":"progress","expected_revision":NaN}')
            )
            before = CoreService.open(root).recover()

            # Then: side work and parse failure remain explicit structured outcomes
            self.assertEqual(context["context"]["side_work"], "SIDE")
            self.assertEqual(invalid["error"]["code"], "INVALID_REQUEST")
            self.assertEqual(duplicate["error"]["code"], "INVALID_REQUEST")
            self.assertEqual(non_finite["error"]["code"], "INVALID_REQUEST")
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_rejecting_side_work_restores_exact_primary_without_publication(self) -> None:
        # Given: active primary work whose exact context is preserved before unrelated side work
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init(expected_revision=ExpectedRevision(0))
            workflow.new("Primary capability", expected_revision=ExpectedRevision(workflow.status().revision))
            workflow.record_progress(
                "implement", "primary", (), "work", (), "resume",
                expected_revision=ExpectedRevision(workflow.status().revision),
            )
            primary = CoreService.open(root).recover().active_context
            if primary is None:
                self.fail("expected preserved primary context")
            publications = CoreService.open(root).recover().publications
            history = CoreService.open(root).recover().publication_history
            side_revision = activate_side(root)

            # When: the provider-neutral protocol rejects the active side work at its current revision
            rejected = json.loads(
                execute(root, request("reject", expected_revision=side_revision))
            )

            # Then: the primary is restored exactly, the side is archived, and publication authority is unchanged
            state = CoreService.open(root).recover()
            self.assertEqual(rejected.get("result", {}).get("code"), "PRIMARY_RESTORED")
            self.assertEqual(state.active_context, primary)
            self.assertIsNone(state.preserved_primary_context)
            self.assertEqual(state.publications, publications)
            self.assertEqual(state.publication_history, history)
            self.assertEqual(len(state.terminal_archives), 1)
            self.assertEqual(state.terminal_archives[0].context.side_of, primary.feature_id)

    def test_reject_stale_revision_is_reported_before_terminal_mutation(self) -> None:
        # Given: active side work and a revision captured before an unrelated mutation
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init(expected_revision=ExpectedRevision(0))
            workflow.new("Primary capability", expected_revision=ExpectedRevision(workflow.status().revision))
            workflow.record_progress(
                "implement", "primary", (), "work", (), "resume",
                expected_revision=ExpectedRevision(workflow.status().revision),
            )
            expected_revision = activate_side(root)
            workflow.record_progress(
                "implement", "side", (), "intervening", (), "resume",
                expected_revision=ExpectedRevision(expected_revision),
            )
            before = CoreService.open(root).recover()

            # When: rejection is submitted using the stale revision
            response = json.loads(execute(root, request("reject", expected_revision=expected_revision)))

            # Then: the stale request exposes expected and actual revisions without terminal mutation
            self.assertEqual(response["error"]["code"], "STALE_REVISION")
            self.assertEqual(response["error"]["expected_revision"], expected_revision)
            self.assertEqual(response["error"]["actual_revision"], before.revision)
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_reject_root_payload_is_strict_and_requires_integer_revision(self) -> None:
        # Given: an empty temporary repository
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = CoreService.open(root).recover()

            # When: reject requests omit, mis-type, or surplus the root revision contract
            responses = tuple(
                json.loads(execute(root, payload))
                for payload in (
                    request("reject"),
                    request("reject", expected_revision=True),
                    request("reject", expected_revision=1.5),
                    request("reject", expected_revision=0, unexpected=True),
                )
            )

            # Then: every malformed request is invalid and no Product Truth or history is created
            self.assertTrue(all(item["error"]["code"] == "INVALID_REQUEST" for item in responses))
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_context_serializes_external_sdd_structure_without_mutation(self) -> None:
        # Given: a fresh root with recognized external SDD directories
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".specify").mkdir()
            (root / "openspec" / "specs").mkdir(parents=True)
            WorkflowService.open(root).init()
            before = CoreService.open(root).recover()

            # When: the strict read-only context action is requested
            response = json.loads(execute(root, request("context")))

            # Then: structural facts and completeness metadata are available without a revision change
            context = response["context"]
            self.assertEqual(
                context["external_sdd_evidence"],
                [
                    {"framework": "OPENSPEC", "paths": ["openspec/specs/"]},
                    {"framework": "SPEC_KIT", "paths": [".specify/"]},
                ],
            )
            self.assertEqual(context["external_sdd_evidence_total"], 2)
            self.assertTrue(context["external_sdd_evidence_complete"])
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_context_reports_incomplete_external_sdd_evidence_without_mutation(self) -> None:
        # Given: recognized Spec-Kit structure and an unreadable direct command probe
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".specify").mkdir()
            (root / ".opencode" / "commands").mkdir(parents=True)
            WorkflowService.open(root).init()
            before = CoreService.open(root).recover()

            # When: a read-only context request reaches a failed direct command probe
            with patch(
                "evospec.discovery.os.scandir",
                side_effect=OSError("access denied"),
            ):
                response = json.loads(execute(root, request("context")))

            # Then: partial structural facts are incomplete and Core state is unchanged
            context = response["context"]
            self.assertEqual(
                context["external_sdd_evidence"],
                [{"framework": "SPEC_KIT", "paths": [".specify/"]}],
            )
            self.assertFalse(context["external_sdd_evidence_complete"])
            self.assertEqual(context["revision"], before.revision)
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_context_reports_capped_command_evidence_without_lifecycle_mutation(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands = root / ".opencode" / "commands"
            commands.mkdir(parents=True)
            for index in range(MAX_EXTERNAL_SDD_COMMAND_ENTRIES + 1):
                (commands / f"speckit.command-{index:03}.md").write_text("foreign command", encoding="utf-8")
            WorkflowService.open(root).init()
            before = CoreService.open(root).recover()

            response = json.loads(execute(root, request("context")))

            context = response["context"]
            self.assertEqual(context["external_sdd_evidence"], [])
            self.assertFalse(context["external_sdd_evidence_complete"])
            self.assertEqual(context["revision"], before.revision)
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_select_capability_is_strict_revision_bound_and_preserves_core_statuses(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            core = CoreService.open(root)
            state = core.record_feature(
                core.recover(),
                Feature(FeatureId("feature-export-a"), "Export delivery", LivingSpecId("spec-export-a")),
            )
            core.record_feature(
                state,
                Feature(FeatureId("feature-export-b"), "Export delivery", LivingSpecId("spec-export-b")),
            )
            workflow.change("Change export delivery behavior")
            pending_state = CoreService.open(root).recover()
            selection = pending_state.pending_capability_selection
            if selection is None:
                self.fail("expected Core-issued pending capability selection")
            pending_context = json.loads(execute(root, request("context")))["context"]
            self.assertEqual(pending_context["outcome"], {
                "kind": "HUMAN_MATERIAL_DECISION",
                "message": "a material capability decision is required",
                "intervention": "USER",
                "material_decision": {
                    "decision": "select a capability",
                    "options": [
                        "feature-export-a",
                        "feature-export-b",
                        "CREATE_STANDALONE_CAPABILITY",
                    ],
                },
            })

            malformed = tuple(
                json.loads(execute(root, payload))
                for payload in (
                    request("select_capability", expected_revision=pending_state.revision, choice="feature-export-a"),
                    request("select_capability", expected_revision=pending_state.revision, selection_id=str(selection.id), choice=" "),
                    request("select_capability", expected_revision=pending_state.revision, selection_id=str(selection.id), choice="feature-export-a", assessment={}),
                )
            )
            invalid = json.loads(execute(
                root,
                request("select_capability", expected_revision=pending_state.revision, selection_id=str(selection.id), choice="feature-forged"),
            ))

            self.assertTrue(all(response["error"]["code"] == "INVALID_REQUEST" for response in malformed))
            self.assertEqual(invalid["result"]["code"], "INVALID_CAPABILITY_SELECTION")
            self.assertEqual(invalid["context"]["revision"], pending_state.revision)
            self.assertEqual(CoreService.open(root).recover(), pending_state)

            selected = json.loads(execute(
                root,
                request("select_capability", expected_revision=pending_state.revision, selection_id=str(selection.id), choice="feature-export-a"),
            ))
            selected_state = CoreService.open(root).recover()
            stale = json.loads(execute(
                root,
                request("select_capability", expected_revision=pending_state.revision, selection_id=str(selection.id), choice="feature-export-a"),
            ))
            replay = json.loads(execute(
                root,
                request("select_capability", expected_revision=selected_state.revision, selection_id=str(selection.id), choice="feature-export-a"),
            ))

            self.assertEqual(selected["result"]["code"], "ESTABLISHED_CAPABILITY")
            self.assertEqual(selected["context"]["revision"], selected_state.revision)
            self.assertEqual(stale["error"]["code"], "STALE_REVISION")
            self.assertEqual(replay["result"]["code"], "NO_PENDING_CAPABILITY_SELECTION")
            self.assertEqual(replay["context"]["revision"], selected_state.revision)
            self.assertEqual(CoreService.open(root).recover(), selected_state)

    def test_recover_discovery_is_strict_revision_bound_and_uses_only_core_offered_evidence(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "evidence-one.md").write_text("External Blocker Reconciliation", encoding="utf-8")
            (root / "evidence-two.md").write_text("External Blocker Reconciliation", encoding="utf-8")
            overflow = root / "overflow"
            overflow.mkdir()
            for index in range(129):
                (overflow / f"z-{index:03}.txt").write_text("noise", encoding="utf-8")
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("External Blocker Reconciliation")
            pending_state = CoreService.open(root).recover()
            recovery = pending_state.pending_discovery_recovery
            if recovery is None:
                self.fail("expected Core-issued bounded discovery recovery")
            candidate = recovery.candidates[0]
            evidence_ids: list[JsonValue] = [
                item.id for item in recovery.evidence if item.candidate == candidate
            ]

            malformed = tuple(
                json.loads(execute(root, payload))
                for payload in (
                    request("recover_discovery", expected_revision=pending_state.revision, candidate=candidate, evidence_ids=evidence_ids),
                    request("recover_discovery", expected_revision=pending_state.revision, recovery_id=str(recovery.id), candidate=candidate, evidence_ids="not-an-array"),
                    request("recover_discovery", expected_revision=pending_state.revision, recovery_id=str(recovery.id), candidate=" ", evidence_ids=evidence_ids),
                    request("recover_discovery", expected_revision=pending_state.revision, recovery_id=str(recovery.id), candidate=candidate, evidence_ids=evidence_ids, assessment={}),
                )
            )
            invalid_evidence = json.loads(execute(
                root,
                request("recover_discovery", expected_revision=pending_state.revision, recovery_id=str(recovery.id), candidate=candidate, evidence_ids=["evidence-forged"]),
            ))
            invalid_reference = json.loads(execute(
                root,
                request("recover_discovery", expected_revision=pending_state.revision, recovery_id="recovery-forged", candidate=candidate, evidence_ids=evidence_ids),
            ))

            self.assertTrue(all(response["error"]["code"] == "INVALID_REQUEST" for response in malformed))
            self.assertEqual(invalid_evidence["result"]["code"], "INVALID_DISCOVERY_RECOVERY")
            self.assertEqual(invalid_reference["result"]["code"], "NO_PENDING_DISCOVERY_RECOVERY")
            self.assertEqual(invalid_evidence["context"]["revision"], pending_state.revision)
            self.assertEqual(invalid_reference["context"]["revision"], pending_state.revision)
            self.assertEqual(CoreService.open(root).recover(), pending_state)

            recovered = json.loads(execute(
                root,
                request("recover_discovery", expected_revision=pending_state.revision, recovery_id=str(recovery.id), candidate=candidate, evidence_ids=evidence_ids),
            ))
            recovered_state = CoreService.open(root).recover()
            stale = json.loads(execute(
                root,
                request("recover_discovery", expected_revision=pending_state.revision, recovery_id=str(recovery.id), candidate=candidate, evidence_ids=evidence_ids),
            ))

            self.assertEqual(recovered["result"]["code"], "ESTABLISHED_CAPABILITY")
            self.assertEqual(recovered["context"]["revision"], recovered_state.revision)
            self.assertEqual(stale["error"]["code"], "STALE_REVISION")
            self.assertIsNone(recovered_state.pending_discovery_recovery)

    def test_autonomy_proposal_rejection_explains_missing_finite_budget(self) -> None:
        # The model must get the Core rule that failed, not a generic refusal.
        # An incomplete scope request may not mutate the authoritative state.
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialized = json.loads(execute(root, request("init", expected_revision=0)))
            started = json.loads(execute(
                root,
                request(
                    "new",
                    expected_revision=initialized["result"]["revision"],
                    intent="MiniLista", assessment=assessment("NEW", "PLANNED", "FEATURE"),
                ),
            ))
            revision = started["result"]["revision"]
            before = CoreService.open(root).recover()
            # Strict transport shape errors must stay separate from Core
            # rejections and must not publish any pending decision.
            malformed = json.loads(execute(root, request(
                "propose_autonomy_expansion",
                expected_revision=revision,
                delta={"allowed_paths": ["index.html"]},
            )))
            self.assertEqual(malformed["error"]["code"], "INVALID_REQUEST")
            self.assertIn("missing", malformed["error"]["detail"])
            self.assertEqual(before, CoreService.open(root).recover())

            # No budget was supplied: the proposed envelope still has budget 0.
            invalid = json.loads(execute(root, request(
                "propose_autonomy_expansion",
                expected_revision=revision,
                delta={
                    "allowed_paths": ["index.html"],
                    "reversible_actions": None,
                    "operation_budget": None,
                    "external_wait_allowed": None,
                    "retry_budget": None,
                    "irreversible_permissions": None,
                    "stop_conditions": None,
                },
            )))
            self.assertEqual(invalid["error"]["code"], "WORKFLOW_ERROR")
            self.assertIn(
                "an autonomy grant requires a finite positive operation budget",
                invalid["error"]["detail"],
            )
            self.assertEqual(before, CoreService.open(root).recover())

            # An exact one-file proposal with a finite budget is persisted
            # only as a decision, not as a granted editing permission.
            proposed = json.loads(execute(root, request(
                "propose_autonomy_expansion",
                expected_revision=revision,
                delta={
                    "allowed_paths": ["index.html"],
                    "reversible_actions": ["progress", "assess_locks", "review", "verify", "edit"],
                    "operation_budget": 3,
                    "external_wait_allowed": None,
                    "retry_budget": None,
                    "irreversible_permissions": None,
                    "stop_conditions": None,
                },
            )))
            self.assertEqual(proposed["result"]["code"], "AUTONOMY_EXPANSION_PROPOSED")
            pending = proposed["context"]["pending_autonomy_expansion"]
            self.assertIsNotNone(pending)
            self.assertEqual(pending["delta"]["allowed_paths"], ["index.html"])
            self.assertEqual(pending["delta"]["operation_budget"], 3)
            self.assertEqual(
                proposed["context"]["autonomy_envelope"]["allowed_paths"], [],
                "a proposal must not grant file authority before a human decision",
            )
            self.assertEqual(
                json.loads(execute(root, request(
                    "propose_autonomy_expansion",
                    expected_revision=revision,
                    delta={
                        "allowed_paths": ["index.html"],
                        "operation_budget": 3,
                        "reversible_actions": None,
                        "external_wait_allowed": None,
                        "retry_budget": None,
                        "irreversible_permissions": None,
                        "stop_conditions": None,
                    },
                )))["error"]["code"],
                "STALE_REVISION",
            )

    def test_protocol_projects_autonomy_envelope_metrics_and_evaluation(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialized = json.loads(execute(root, request("init", expected_revision=0)))
            created = json.loads(execute(root, request("new", expected_revision=initialized["result"]["revision"], intent="Export capability", assessment=assessment("NEW", "PLANNED", "FEATURE"))))

            context = created["context"]

            self.assertEqual(context["autonomy_envelope"]["goal"], "Export capability")
            self.assertEqual(context["autonomy_envelope"]["allowed_paths"], [])
            self.assertEqual(context["autonomy_metrics"]["operational_handoffs"], 0)
            self.assertEqual(context["autonomy_evaluation"]["authorization"], "ALLOWED")

            revision = context["revision"]
            before = CoreService.open(root).recover()
            allowed = json.loads(execute(root, request(
                "autonomy_evaluate", expected_revision=revision, operation="progress", paths=[], irreversible=False,
            )))
            outside_scope = json.loads(execute(root, request(
                "autonomy_evaluate", expected_revision=revision, operation="run tests", paths=["tests/test_agent_protocol.py"], irreversible=False,
            )))
            protected = json.loads(execute(root, request(
                "autonomy_evaluate", expected_revision=revision, operation="git push", paths=[], irreversible=False,
            )))
            stale = json.loads(execute(root, request(
                "autonomy_evaluate", expected_revision=revision - 1, operation="progress", paths=[], irreversible=False,
            )))
            malformed = json.loads(execute(root, request(
                "autonomy_evaluate", expected_revision=revision, operation="progress", paths=["../outside"], irreversible=False,
            )))

            self.assertEqual(allowed["evaluation"]["authorization"], "ALLOWED")
            self.assertEqual(outside_scope["evaluation"]["authorization"], "HUMAN_MATERIAL_DECISION")
            self.assertEqual(protected["evaluation"]["authorization"], "HUMAN_MATERIAL_DECISION")
            self.assertEqual(stale["error"]["code"], "STALE_REVISION")
            self.assertEqual(malformed["error"]["code"], "INVALID_REQUEST")
            self.assertEqual(CoreService.open(root).recover(), before)
