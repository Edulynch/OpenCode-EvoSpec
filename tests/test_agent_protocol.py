from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from evospec.agent_protocol import execute
from evospec.discovery import MAX_EXTERNAL_SDD_COMMAND_ENTRIES
from evospec.domain import (
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
    VerificationEvidence,
    WorkRelation,
    WorkRelationKind,
)
from evospec.lifecycle import Classification, ReviewVerdict
from evospec.persistence import JsonValue
from evospec.services import CoreService
from evospec.workflow import WorkflowService
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
