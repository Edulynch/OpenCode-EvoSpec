"""Execute provider-neutral JSON requests over ``WorkflowService``."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .agent import (
    AcceptedSliceContext,
    AgentContext,
    HistoricalSliceAcceptance,
    ProvisionalSliceInventory,
    SliceAcceptanceItem,
    WorkItemSummary,
    project_context,
)
from .continuation_summary import ContinuationSummary, criterion_verification, project_continuation
from .agent_protocol_schema import (
    ProtocolError,
    decode_request,
    parse_action,
    parse_autonomy_envelope_delta,
    parse_autonomy_expansion_decision,
    parse_bounded_strings,
    parse_boolean,
    parse_clarification_answers,
    parse_convergence_assessment,
    parse_expected_revision,
    parse_continuity_request,
    parse_test_exit_code,
    parse_irreversible_operation_kind,
    parse_git_history_rewrite_policy,
    parse_git_commit_rewrites,
    parse_intent_assessment,
    parse_lock_assessments,
    parse_nonblank_string,
    parse_nonblank_strings,
    parse_provisional_slice_criteria,
    parse_provisional_slice_head,
    parse_provisional_slice_texts,
    parse_input_fingerprint,
    parse_input_snapshot,
    parse_narrowed_autonomy_envelope,
    parse_operational_state,
    parse_optional_string,
    parse_review_result,
    parse_repository_paths,
    parse_string,
    parse_strings,
    parse_verification_evidence,
    parse_work_filter,
    parse_work_relation_kind,
    validate_action_request,
)
from .domain import AssessmentOperation, AutonomyEnvelope, AutonomyEnvelopeDelta, ContinuityError, CriterionCoverageApproval, CriterionTestReceipt, SliceCriteriaRevision, SliceOutcomeReceipt
from .input_fingerprint import (
    COVERAGE_CONTRACT_VERSION,
    INPUT_FINGERPRINT_VERSION,
    INPUT_POLICY_DIGEST,
    INPUT_POLICY_ID,
    TEST_ADMISSION_POLICY_DIGEST,
    TEST_ADMISSION_POLICY_ID,
    R3B_COVERAGE_CONTRACT,
    TEST_ENVIRONMENT_PROFILE_DIGEST,
    TEST_ENVIRONMENT_PROFILE_ID,
    InputSnapshot,
)
from .persistence import JsonValue, RecoveryError
from .services import (
    RequestScopedObjectiveStatus,
    ServiceError,
    StaleRevisionError,
    SessionRequestEvidence,
)
from .workflow import (
    ClarificationAnswer,
    WorkflowError,
    WorkflowResult,
    WorkflowService,
)


def encode_success(
    result: WorkflowResult, context: AgentContext, *, include_continuation: bool = False,
    persisted_schema: int | None = None,
) -> str:
    payload = context_json(context, persisted_schema=persisted_schema)
    if include_continuation:
        payload["continuation"] = _continuation_json(project_continuation(context))
    return _encode({"result": _result_json(result), "context": payload})


def encode_error(code: str, detail: str) -> str:
    return _encode({"error": {"code": code, "detail": detail}})


def encode_stale_revision(detail: str, expected_revision: int, actual_revision: int) -> str:
    return _encode({"error": {"code": "STALE_REVISION", "detail": detail,
                                "expected_revision": expected_revision,
                                "actual_revision": actual_revision}})


def _encode(value: dict[str, JsonValue]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _result_json(result: WorkflowResult) -> dict[str, JsonValue]:
    value: dict[str, JsonValue] = {
        "code": result.code, "revision": result.revision, "feature_id": result.feature_id,
        "change_id": result.change_id, "phase": result.phase, "message": result.message,
        "questions": [{"id": question.id, "prompt": question.prompt, "options": list(question.options)} for question in result.questions],
        "pending_intake_id": result.pending_intake_id, "focus": result.focus,
        "current_work": result.current_work, "pending_work": list(result.pending_work),
        "blocker": result.blocker, "classification": result.classification,
        "review_verdict": result.review_verdict, "verification_sufficient": result.verification_sufficient,
        "review_depth": result.review_depth, "verification_depth": result.verification_depth,
        "convergence_depth": result.convergence_depth,
    }
    if result.slice_outcome_receipt is not None:
        value["slice_outcome_receipt"] = _slice_outcome_receipt_json(result.slice_outcome_receipt)
    return value


def _slice_outcome_receipt_json(receipt: SliceOutcomeReceipt) -> dict[str, JsonValue]:
    return {
        "id": receipt.id,
        "repository_id": receipt.repository_id,
        "slice_agreement_ref": receipt.slice_agreement_ref,
        "criteria_revision_id": receipt.criteria_revision_id,
        "evidence_receipt_ids": list(receipt.evidence_receipt_ids),
        "input_fingerprint": receipt.input_fingerprint,
        "input_policy_id": receipt.input_policy_id,
        "input_policy_digest": receipt.input_policy_digest,
        "admission_policy_id": receipt.admission_policy_id,
        "admission_policy_digest": receipt.admission_policy_digest,
        "relevant_drift_digest": receipt.relevant_drift_digest,
        "basis": receipt.basis,
        "source": receipt.source,
        "source_revision": receipt.source_revision,
        "recorded_revision": receipt.recorded_revision,
        "coverage_contract_digest": receipt.coverage_contract_digest,
    }


def _slice_outcome_receipt_json(receipt: SliceOutcomeReceipt) -> dict[str, JsonValue]:
    return {
        "id": receipt.id,
        "repository_id": receipt.repository_id,
        "slice_agreement_ref": receipt.slice_agreement_ref,
        "criteria_revision_id": receipt.criteria_revision_id,
        "evidence_receipt_ids": list(receipt.evidence_receipt_ids),
        "input_fingerprint": receipt.input_fingerprint,
        "input_policy_id": receipt.input_policy_id,
        "input_policy_digest": receipt.input_policy_digest,
        "admission_policy_id": receipt.admission_policy_id,
        "admission_policy_digest": receipt.admission_policy_digest,
        "relevant_drift_digest": receipt.relevant_drift_digest,
        "basis": receipt.basis,
        "source": receipt.source,
        "source_revision": receipt.source_revision,
        "recorded_revision": receipt.recorded_revision,
        "coverage_contract_digest": receipt.coverage_contract_digest,
    }


def _coverage_approval_json(approval: CriterionCoverageApproval) -> dict[str, JsonValue]:
    return {
        "id": approval.id,
        "contract_digest": approval.contract_digest,
        "repository_id": approval.repository_id,
        "slice_agreement_ref": approval.slice_agreement_ref,
        "criteria_revision_id": approval.criteria_revision_id,
        "criterion_id": approval.criterion_id,
        "test_kind": approval.test_kind,
        "test_target": approval.test_target,
        "known_inputs": [
            {"kind": item.kind, "path": item.path}
            for item in approval.known_inputs
        ],
        "root_fingerprint": approval.root_fingerprint,
        "input_policy_id": approval.input_policy_id,
        "input_policy_digest": approval.input_policy_digest,
        "admission_policy_id": approval.admission_policy_id,
        "admission_policy_digest": approval.admission_policy_digest,
        "environment_profile_id": approval.environment_profile_id,
        "environment_profile_policy_digest": approval.environment_profile_policy_digest,
        "statement": approval.statement,
        "source_reference": approval.source_reference,
        "source": approval.source,
        "source_revision": approval.source_revision,
        "recorded_revision": approval.recorded_revision,
    }


def _autonomy_envelope_json(envelope: AutonomyEnvelope) -> dict[str, JsonValue]:
    return {
        "goal": envelope.goal,
        "allowed_paths": [str(path) for path in envelope.allowed_paths],
        "operation_budget": envelope.operation_budget,
        "reversible_actions": list(envelope.reversible_actions),
        "external_wait_allowed": envelope.external_wait_allowed,
        "retry_budget": envelope.retry_budget,
        "irreversible_permissions": list(envelope.irreversible_permissions),
        "stop_conditions": list(envelope.stop_conditions),
    }


def _autonomy_delta_json(delta: AutonomyEnvelopeDelta) -> dict[str, JsonValue]:
    return {
        "allowed_paths": None if delta.allowed_paths is None else [str(path) for path in delta.allowed_paths],
        "operation_budget": delta.operation_budget,
        "reversible_actions": None if delta.reversible_actions is None else list(delta.reversible_actions),
        "external_wait_allowed": delta.external_wait_allowed,
        "retry_budget": delta.retry_budget,
        "irreversible_permissions": None if delta.irreversible_permissions is None else list(delta.irreversible_permissions),
        "stop_conditions": None if delta.stop_conditions is None else list(delta.stop_conditions),
    }


def _accepted_slice_json(accepted: AcceptedSliceContext | None) -> dict[str, JsonValue] | None:
    # Active and parked Tasks use the same contract, including the current
    # criteria head, without promoting parked work into active authority.
    if accepted is None:
        return None
    value: dict[str, JsonValue] = {
        "feature_agreement_ref": accepted.feature_agreement_ref,
        "slice_agreement_ref": accepted.slice_agreement_ref,
        "feature_title": accepted.feature_title,
        "slice_title": accepted.slice_title,
        "task_id": accepted.task_id,
        "initial_task": accepted.initial_task,
        "checkpoint": accepted.checkpoint,
        "source": accepted.source,
        "source_reference": accepted.source_reference,
        "criteria_status": accepted.criteria_status,
        "criteria_revision": _criteria_revision_json(accepted.criteria_revision),
    }
    if accepted.session_request is not None:
        value["session_request"] = {
            "statement": accepted.session_request.statement,
            "sha256": accepted.session_request.sha256,
            "repository_id": accepted.session_request.repository_id,
        }
    return value


def _criteria_revision_json(revision: SliceCriteriaRevision | None) -> dict[str, JsonValue] | None:
    if revision is None:
        return None
    return {
        "id": revision.id,
        "previous_revision_id": revision.previous_revision_id,
        "outcome": revision.outcome,
        "example": revision.example,
        "inclusions": list(revision.inclusions),
        "exclusions": list(revision.exclusions),
        "decisions": list(revision.decisions),
        "criteria": [
            {"id": item.id, "expected_result": item.expected_result,
             "expected_evidence": item.expected_evidence,
             **({"provenance": item.provenance} if item.provenance is not None else {})}
            for item in revision.criteria
        ],
        "source": revision.source,
        "source_reference": revision.source_reference,
        "reason": revision.reason,
    }


def _request_scoped_objective_json(
    objective: RequestScopedObjectiveStatus,
) -> dict[str, JsonValue]:
    kind = getattr(objective, "kind", None)
    value: dict[str, JsonValue] = {
        "status": objective.status,
        "source_reference": objective.source_reference,
        "request_sha256": objective.request_sha256,
        "blockers": list(objective.blockers),
        "heading": objective.heading,
    }
    if kind is not None:
        value["kind"] = kind
        value["criteria"] = [
            {"operation": operation, "provenance": provenance}
            for operation, provenance in getattr(objective, "criteria", ())
        ]
    mode = getattr(objective, "mode", None)
    if mode is not None:
        value["mode"] = mode
    return value


def _slice_acceptance_item_json(item: SliceAcceptanceItem) -> dict[str, JsonValue]:
    return {
        "slice_agreement_ref": item.slice_agreement_ref,
        "feature_id": item.feature_id,
        "feature_agreement_ref": item.feature_agreement_ref,
        "feature_title": item.feature_title,
        "slice_title": item.slice_title,
        "authority_status": item.authority_status,
        "scope_status": item.scope_status,
        "provenance": item.provenance,
        "operational_state": item.operational_state,
        "criteria_status": item.criteria_status,
        "criteria_revision": _criteria_revision_json(item.criteria_revision),
        "historical_receipt": item.historical_receipt,
        "historical_acceptance_status": item.historical_acceptance_status,
        "historical_acceptance": (
            None if item.historical_acceptance is None
            else _historical_slice_acceptance_json(item.historical_acceptance)
        ),
    }


def _historical_slice_acceptance_json(
    acceptance: HistoricalSliceAcceptance,
) -> dict[str, JsonValue]:
    return {
        "receipt": _slice_outcome_receipt_json(acceptance.receipt),
        "criteria_revision": _criteria_revision_json(acceptance.criteria_revision),
        "criterion_evidence": [
            {
                "id": proof.id,
                "slice_agreement_ref": proof.slice_agreement_ref,
                "criteria_revision_id": proof.criteria_revision_id,
                "criterion_id": proof.criterion_id,
                "test_kind": proof.test_kind,
                "test_target": proof.test_target,
                "exit_code": proof.exit_code,
                "output_sha256": proof.output_sha256,
                "source_revision": proof.source_revision,
                "recorded_revision": proof.recorded_revision,
            }
            for proof in acceptance.criterion_evidence
            if isinstance(proof, CriterionTestReceipt)
        ],
    }


def _slice_acceptance_inventory_json(context: AgentContext) -> dict[str, JsonValue]:
    inventory = context.slice_acceptance_inventory
    return {
        "source": inventory.source,
        "total": inventory.total,
        "complete": inventory.complete,
        "truncated": inventory.truncated,
        "history_scope": inventory.history_scope,
        "items": [_slice_acceptance_item_json(item) for item in inventory.items],
    }


def _provisional_slice_inventory_json(
    inventory: ProvisionalSliceInventory,
) -> dict[str, JsonValue]:
    return {
        "source": inventory.source,
        "availability": inventory.availability,
        "total": inventory.total,
        "complete": inventory.complete,
        "truncated": inventory.truncated,
        "items": [
            {
                "proposal_id": item.proposal_id,
                "head": {
                    "revision_id": item.head_revision_id,
                    "revision": item.head_revision,
                    "digest": item.head_digest,
                },
                "repository_id": item.repository_id,
                "feature_id": item.feature_id,
                "title": item.title,
                "status": item.status,
                "provenance": item.provenance,
                "criteria": [
                    {
                        "id": criterion.id,
                        "expected_result": criterion.expected_result,
                        "expected_evidence": criterion.expected_evidence,
                    }
                    for criterion in item.criteria
                ],
            }
            for item in inventory.items
        ],
    }


def _continuation_json(summary: ContinuationSummary) -> dict[str, JsonValue]:
    accepted = summary.accepted_slice
    task_progress = summary.task_progress
    return {
        "status": summary.status,
        "feature_id": summary.feature_id,
        "feature_title": summary.feature_title,
        "lifecycle": None if summary.lifecycle is None else list(summary.lifecycle),
        "active_slice": None if summary.active_slice is None else {
            "slice_agreement_ref": summary.active_slice.slice_agreement_ref,
            "slice_title": summary.active_slice.slice_title,
            "feature_id": summary.active_slice.feature_id,
            "scope_status": summary.active_slice.scope_status,
            "provenance": summary.active_slice.provenance,
            "criteria_status": summary.active_slice.criteria_status,
            "criteria_revision_id": (
                None if summary.active_slice.criteria_revision is None
                else summary.active_slice.criteria_revision.id
            ),
        },
        "task": None if accepted is None else {
            "task_id": accepted.task_id,
            "initial_task": accepted.initial_task,
            "initial_checkpoint": accepted.checkpoint,
            "current_work": summary.current_work,
        },
        "task_report": None if task_progress is None else {
            "task_id": task_progress.task_id,
            "checkpoint": task_progress.checkpoint,
            "report_kind": task_progress.report_kind,
            "recorded_revision": task_progress.recorded_revision,
        },
        "criterion_verification": [
            {key: item[key] for key in ("criterion_id", "status", "evidence_source", "fresh_for_current_workspace")}
            for item in summary.criterion_verification if isinstance(item, dict)
        ] if isinstance(summary.criterion_verification, list) else None,
        "slice_outcome_status": summary.slice_outcome_status,
        "terminal_history": summary.terminal_history,
        "decision": None if summary.decision is None else {
            "prompt": summary.decision.decision,
            "options": list(summary.decision.options),
        },
        "next": {
            "code": summary.next_code,
            "action": summary.next_action,
            "action_allowed": summary.next_action_allowed,
            "message": summary.next_message,
            "required_input": list(summary.required_input),
            "options_kind": summary.next_options_kind,
            "options": list(summary.next_options),
        },
        "other_slices": [
            {
                "slice_agreement_ref": item.slice_agreement_ref,
                "feature_id": item.feature_id,
                "feature_title": item.feature_title,
                "slice_title": item.slice_title,
                "scope_status": item.scope_status,
                "provenance": item.provenance,
                "criteria_status": item.criteria_status,
                "criteria_revision_id": (
                    None if item.criteria_revision is None else item.criteria_revision.id
                ),
            }
            for item in summary.other_slices
        ],
        "inventory": {
            "visible": summary.inventory_visible,
            "total": summary.inventory_total,
            "complete": summary.inventory_complete,
            "truncated": summary.inventory_truncated,
            "history_scope": summary.inventory_history_scope,
        },
        "work_queue": {
            "visible": summary.queue_visible,
            "total": summary.queue_total,
            "complete": summary.queue_complete,
            "relations_total": summary.queue_relations_total,
            "relations_complete": summary.queue_relations_complete,
            "next_code": summary.queue_next_code,
            "next_candidates_total": summary.queue_candidates_total,
            "next_candidates_complete": summary.queue_candidates_complete,
        },
    }


def context_json(
    context: AgentContext, *, persisted_schema: int | None = None,
) -> dict[str, JsonValue]:
    """Return the stable JSON-safe representation of an agent context."""
    payload: dict[str, JsonValue] = {"revision": context.revision, "repository_id": context.repository_id, "initialization_state": context.initialization_state,
            "r3_schema_ready": context.r3_schema_ready,
            "r3b_schema_ready": context.r3b_schema_ready,
            "r3b_policy": None if not context.r3b_schema_ready else {
                "coverage_contract_version": COVERAGE_CONTRACT_VERSION,
                "input_version": INPUT_FINGERPRINT_VERSION,
                "input_policy_id": INPUT_POLICY_ID,
                "input_policy_digest": INPUT_POLICY_DIGEST,
                "admission_policy_id": TEST_ADMISSION_POLICY_ID,
                "admission_policy_digest": TEST_ADMISSION_POLICY_DIGEST,
                "environment_profile_id": TEST_ENVIRONMENT_PROFILE_ID,
                "environment_profile_policy_digest": TEST_ENVIRONMENT_PROFILE_DIGEST,
                "coverage_contract": R3B_COVERAGE_CONTRACT,
            },
            "e1_schema_ready": context.e1_schema_ready,
            "provisional_slice_schema": persisted_schema,
            "provisional_slice_read_ready": (
                context.provisional_slice_inventory.availability == "AVAILABLE"
            ),
            "provisional_slice_activation_available": (
                "activate_provisional_slice_writes" in context.legal_actions
            ),
            "provisional_slice_write_ready": (
                "propose_provisional_slice" in context.legal_actions
            ),
            "accepted_slice": _accepted_slice_json(context.accepted_slice),
            "slice_acceptance_inventory": _slice_acceptance_inventory_json(context),
            "provisional_slice_inventory": _provisional_slice_inventory_json(
                context.provisional_slice_inventory,
            ),
            "feature_completion": None if context.feature_completion is None else {
                "feature_id": context.feature_completion.feature_id,
                "status": context.feature_completion.status,
                "receipt_id": context.feature_completion.receipt_id,
                "blockers": list(context.feature_completion.blockers),
                "slice_agreement_refs": list(context.feature_completion.slice_agreement_refs),
                "cycle_id": context.feature_completion.cycle_id,
                "cycle_state": context.feature_completion.cycle_state,
                "reopen_receipt_id": context.feature_completion.reopen_receipt_id,
                "history": [
                    {"cycle_id": cycle, "reopen_receipt_id": reopen_id,
                     "completion_receipt_id": closure_id}
                    for cycle, reopen_id, closure_id in context.feature_completion.history
                ],
            },
            "input_snapshot": None if context.input_snapshot is None else {
                "version": context.input_snapshot.version,
                "policy_id": context.input_snapshot.policy_id,
                "policy_digest": context.input_snapshot.policy_digest,
                "repository_id": context.input_snapshot.repository_id,
                "fingerprint": context.input_snapshot.fingerprint,
                "file_count": context.input_snapshot.file_count,
                "directory_count": context.input_snapshot.directory_count,
                "total_bytes": context.input_snapshot.total_bytes,
            },
            "request_scoped_objective": None
            if context.request_scoped_objective is None
            else _request_scoped_objective_json(context.request_scoped_objective),
            "r3b_coverage_approvals": [
                _coverage_approval_json(item)
                for item in context.criterion_coverage_approvals
            ],
            "slice_outcome": None if context.slice_outcome is None else {
                "status": context.slice_outcome.status,
                "latest_receipt_id": context.slice_outcome.latest_receipt_id,
                "blockers": list(context.slice_outcome.blockers),
                "receipt": None if context.slice_outcome.receipt is None
                else _slice_outcome_receipt_json(context.slice_outcome.receipt),
            },
            "criterion_verification": criterion_verification(context),
            "lifecycle": None if context.lifecycle is None else list(context.lifecycle),
            "feature_id": context.feature_id, "change_id": context.change_id, "classification": context.classification,
            "progress": {"current_action": context.progress.current_action, "focus": context.progress.focus,
                          "completed_work": list(context.progress.completed_work), "current_work": context.progress.current_work,
                          "pending_work": list(context.progress.pending_work), "resume_point": context.progress.resume_point},
            "task_progress": None if context.task_progress is None else {
                "task_id": context.task_progress.task_id,
                "checkpoint": context.task_progress.checkpoint,
                "statement": context.task_progress.statement,
                "report_kind": context.task_progress.report_kind,
                "source_kind": context.task_progress.source_kind,
                "source_reference": context.task_progress.source_reference,
                "evidence_refs": list(context.task_progress.evidence_refs),
                "recorded_revision": context.task_progress.recorded_revision,
            },
             "decision_state": {"unresolved_material_decision": context.decision_state.unresolved_material_decision,
                                "clarification_count": context.decision_state.clarification_count,
                                "constraint_count": context.decision_state.constraint_count,
                                "review_verdict": context.decision_state.review_verdict,
                                 "verification_sufficient": context.decision_state.verification_sufficient},
              "outcome": None if context.outcome is None else {
                  "kind": context.outcome.kind.value, "message": context.outcome.message,
                  "intervention": context.outcome.intervention.value,
                  "material_decision": None if context.outcome.material_decision is None else {
                      "decision": context.outcome.material_decision.decision,
                      "options": list(context.outcome.material_decision.options),
                  },
              },
               "side_work": context.side_work, "side_work_active": context.side_work_active,
              "pending_intake": context.pending_intake, "stale": context.stale, "stale_reason": context.stale_reason,
              "autonomy_envelope": None if context.autonomy_envelope is None else {
                  "goal": context.autonomy_envelope.goal,
                  "allowed_paths": [str(path) for path in context.autonomy_envelope.allowed_paths],
                  "operation_budget": context.autonomy_envelope.operation_budget,
                  "reversible_actions": list(context.autonomy_envelope.reversible_actions),
                  "external_wait_allowed": context.autonomy_envelope.external_wait_allowed,
                  "retry_budget": context.autonomy_envelope.retry_budget,
                  "irreversible_permissions": list(context.autonomy_envelope.irreversible_permissions),
                  "stop_conditions": list(context.autonomy_envelope.stop_conditions),
              },
              "autonomy_metrics": {
                  "human_material_decisions": context.autonomy_metrics.human_material_decisions,
                  "operational_handoffs": context.autonomy_metrics.operational_handoffs,
                  "automatic_operations": context.autonomy_metrics.automatic_operations,
              },
              "autonomy_evaluation": None if context.autonomy_evaluation is None else {
                  "authorization": context.autonomy_evaluation.authorization.value,
                  "reason": context.autonomy_evaluation.reason,
              },
              "irreversible_authorizations": [
                  {
                      "id": item.id,
                      "operation_kind": item.operation_kind,
                      "feature_id": item.feature_id,
                      "change_id": item.change_id,
                      "target_ref": item.target_ref,
                      "expected_remote_sha": item.expected_remote_sha,
                      "rewrite_policy": item.rewrite_policy,
                      "target_identity_name": item.target_identity_name,
                      "target_identity_email": item.target_identity_email,
                      "expected_result_sha": item.expected_result_sha,
                      "commit_rewrites": [
                          {
                              "old_sha": rewrite.old_sha,
                              "new_sha": rewrite.new_sha,
                              "identity_normalized": rewrite.identity_normalized,
                          }
                          for rewrite in item.commit_rewrites
                      ],
                      "rewrite_plan_digest": item.rewrite_plan_digest,
                      "purpose": item.purpose,
                      "force_with_lease": item.force_with_lease,
                      "one_shot": item.one_shot,
                      "authorized_revision": item.authorized_revision,
                      "status": item.status,
                      "validated_revision": item.validated_revision,
                      "consumed_revision": item.consumed_revision,
                      "resulting_remote_sha": item.resulting_remote_sha,
                      "invalidated_revision": item.invalidated_revision,
                      "invalidation_reason": item.invalidation_reason,
                  }
                  for item in context.irreversible_authorizations
              ],
               "pending_autonomy_expansion": None if context.pending_autonomy_expansion is None else {
                   "proposal_id": context.pending_autonomy_expansion.proposal_id,
                   "repository_id": context.pending_autonomy_expansion.repository_id,
                   "feature_id": context.pending_autonomy_expansion.feature_id,
                   "change_id": context.pending_autonomy_expansion.change_id,
                   "requested_revision": context.pending_autonomy_expansion.requested_revision,
                   "base_envelope": _autonomy_envelope_json(context.pending_autonomy_expansion.base_envelope),
                   "delta": _autonomy_delta_json(context.pending_autonomy_expansion.delta),
                   "proposed_envelope": _autonomy_envelope_json(context.pending_autonomy_expansion.proposed_envelope),
                   "decision_options": ["AUTHORIZE", "REJECT", "NARROW"],
               },
                "work_queue": _work_queue_json(context),
               "legal_actions": list(context.legal_actions),
              "guidance": [
                  {"action": item.action.value, "allowed": item.allowed,
                   "reason_code": item.reason_code, "blocker": item.blocker,
                   "required_actions": [value.value for value in item.required_actions],
                   "next_action": None if item.next_action is None else item.next_action.value,
                   "intervention": item.intervention.value, "scope": list(item.scope),
                   "message": item.message}
                  for item in context.guidance
              ],
             "accepted_capabilities": [
                 {"feature_id": item.feature_id, "title": item.title, "capability": item.capability,
                  "living_spec_id": item.living_spec_id, "publication_revision": item.publication_revision,
                  "publication_path": item.publication_path}
                 for item in context.accepted_capabilities
             ], "accepted_capabilities_total": context.accepted_capabilities_total,
             "accepted_capabilities_complete": context.accepted_capabilities_complete,
             "active_locks": [
                 {"lock_id": item.lock_id, "owner_feature_id": item.owner_feature_id,
                  "lock_revision": item.lock_revision, "baseline_feature_id": item.baseline_feature_id,
                  "baseline_living_spec_id": item.baseline_living_spec_id,
                  "baseline_revision": item.baseline_revision, "baseline_path": item.baseline_path,
                  "protected_aspects": list(item.protected_aspects)}
                 for item in context.active_locks
              ], "active_locks_total": context.active_locks_total,
               "active_locks_complete": context.active_locks_complete,
               "external_sdd_evidence": [
                   {"framework": item.framework, "paths": list(item.paths)}
                   for item in context.external_sdd_evidence
               ], "external_sdd_evidence_total": context.external_sdd_evidence_total,
                "external_sdd_evidence_complete": context.external_sdd_evidence_complete,
                "completed_terminal": context.completed_terminal,
                "pending_discovery_recovery": None if context.pending_discovery_recovery is None else {
                    "recovery_id": context.pending_discovery_recovery.recovery_id,
                    "candidates": [
                        {"candidate": item.candidate, "evidence_ids": list(item.evidence_ids)}
                        for item in context.pending_discovery_recovery.candidates
                    ],
                },
                "pending_capability_selection": None if context.pending_capability_selection is None else {
                    "selection_id": context.pending_capability_selection.selection_id,
                    "choices": list(context.pending_capability_selection.choices),
                }}
    if context.questions:
        payload["questions"] = [
            {"id": question.id, "prompt": question.prompt, "options": list(question.options)}
            for question in context.questions
        ]
    return payload


def _work_queue_json(context: AgentContext) -> dict[str, JsonValue]:
    queue = context.work_queue
    def item(summary: WorkItemSummary) -> dict[str, JsonValue]:
        return {
            "id": summary.id, "operational_state": summary.operational_state,
            "feature_id": summary.feature_id, "change_id": summary.change_id,
            "lifecycle": None if summary.lifecycle is None else list(summary.lifecycle),
            "reason": summary.reason, "transitioned_at": summary.transitioned_at,
            "review_verdict": summary.review_verdict,
            "verification_sufficient": summary.verification_sufficient,
            "activation_eligible": summary.activation_eligible, "blocker": summary.blocker,
            "accepted_slice": _accepted_slice_json(summary.accepted_slice),
        }
    return {
        "all": [item(value) for value in queue.all], "pending": [item(value) for value in queue.pending],
        "actionable": [item(value) for value in queue.actionable], "blocked": [item(value) for value in queue.blocked],
        "frozen": [item(value) for value in queue.frozen], "waiting": [item(value) for value in queue.waiting],
        "active": None if queue.active is None else item(queue.active), "next": queue.next,
        "next_code": queue.next_code, "next_candidates": list(queue.next_candidates),
        "next_candidates_total": queue.next_candidates_total,
        "next_candidates_complete": queue.next_candidates_complete,
        "relations": [{"predecessor_id": value.predecessor_id, "successor_id": value.successor_id, "kind": value.kind} for value in queue.relations],
        "total": queue.total, "complete": queue.complete,
        "relations_total": queue.relations_total, "relations_complete": queue.relations_complete,
    }


def execute(root: Path, payload: str) -> str:
    """Execute one JSON request against the root's canonical workflow facade."""
    try:
        request = decode_request(payload)
        action = parse_action(request)
        if action == "continuity":
            validate_action_request(request, "continuity")
            operation, expected_revision, session_id, data = parse_continuity_request(request)
            return _encode({
                "continuity": WorkflowService.open(root).continuity(
                    operation, expected_revision, session_id, data,
                ),
            })
        workflow = WorkflowService.open(root)
        if action == "autonomy_evaluate":
            return _execute_autonomy_evaluation(workflow, request)
        if action == "context":
            validate_action_request(request, "context")
            inspection = workflow.inspect_locks()
            return encode_success(
                workflow.status(inspection.state), project_context(workflow, inspection),
                include_continuation=True,
                persisted_schema=inspection.persisted_schema,
            )
        result = _dispatch(workflow, request)
        inspection = workflow.inspect_locks()
        return encode_success(
            result, project_context(workflow, inspection),
            persisted_schema=inspection.persisted_schema,
        )
    except ProtocolError as error:
        return encode_error("INVALID_REQUEST", str(error))
    except ContinuityError as error:
        return encode_error(error.code, str(error))
    except StaleRevisionError as error:
        return encode_stale_revision(
            str(error), error.expected_revision, error.actual_revision
        )
    except (WorkflowError, ServiceError, RecoveryError) as error:
        return encode_error("WORKFLOW_ERROR", str(error))


def _execute_autonomy_evaluation(
    workflow: WorkflowService, request: dict[str, JsonValue]
) -> str:
    """Evaluate one proposed operation against the current Core envelope without mutation."""
    validate_action_request(request, "autonomy_evaluate")
    expected_revision = parse_expected_revision(request)
    inspection = workflow.inspect_locks()
    if inspection.state.revision != expected_revision:
        raise StaleRevisionError(expected_revision, inspection.state.revision)
    evaluation = workflow.autonomy_evaluation(
        inspection.state,
        parse_nonblank_string(request, "operation"),
        parse_repository_paths(request, "paths"),
        irreversible=parse_boolean(request, "irreversible"),
    )
    context = project_context(workflow, inspection)
    return _encode(
        {
            "evaluation": None
            if evaluation is None
            else {
                "authorization": evaluation.authorization.value,
                "reason": evaluation.reason,
            },
            "context": context_json(context, persisted_schema=inspection.persisted_schema),
        }
    )


def _dispatch(
    workflow: WorkflowService, request: dict[str, JsonValue]
) -> WorkflowResult:
    action = parse_action(request)
    validate_action_request(request, action)
    match action:
        case "context":
            return workflow.status()
        case "autonomy_evaluate":
            raise ProtocolError("autonomy_evaluate is a read-only protocol query")
        case "propose_autonomy_expansion":
            return workflow.propose_autonomy_expansion(
                parse_autonomy_envelope_delta(request),
                expected_revision=parse_expected_revision(request),
            )
        case "resolve_autonomy_expansion":
            return workflow.resolve_autonomy_expansion(
                parse_nonblank_string(request, "proposal_id"),
                parse_autonomy_expansion_decision(request),
                parse_narrowed_autonomy_envelope(request),
                expected_revision=parse_expected_revision(request),
            )
        case "init":
            return workflow.init(expected_revision=parse_expected_revision(request))
        case "accept_first_slice_session":
            return workflow.accept_first_slice_session(
                SessionRequestEvidence(
                    session_id=parse_nonblank_string(request, "session_id"),
                    message_id=parse_nonblank_string(request, "message_id"),
                    statement=parse_nonblank_string(request, "request_text"),
                    repository_id=parse_nonblank_string(request, "repository_id"),
                ),
                expected_revision=parse_expected_revision(request),
            )
        case "request_scoped_objective":
            return workflow.request_scoped_objective(
                SessionRequestEvidence(
                    session_id=parse_nonblank_string(request, "session_id"),
                    message_id=parse_nonblank_string(request, "message_id"),
                    statement=parse_nonblank_string(request, "request_text"),
                    repository_id=parse_nonblank_string(request, "repository_id"),
                ),
                parse_input_fingerprint(request),
                expected_revision=parse_expected_revision(request),
            )
        case "new":
            return workflow.new(
                parse_string(request, "intent"),
                parse_intent_assessment(request, AssessmentOperation.NEW),
                expected_revision=parse_expected_revision(request),
            )
        case "change":
            return workflow.change(
                parse_string(request, "intent"),
                parse_intent_assessment(request, AssessmentOperation.CHANGE),
                expected_revision=parse_expected_revision(request),
            )
        case "clarify":
            return workflow.clarify(
                tuple(ClarificationAnswer(question_id, answer) for question_id, answer in parse_clarification_answers(request)),
                expected_revision=parse_expected_revision(request),
            )
        case "continue":
            return workflow.continue_work(
                expected_revision=parse_expected_revision(request)
            )
        case "park":
            return workflow.park(
                parse_nonblank_string(request, "reason"),
                expected_revision=parse_expected_revision(request),
            )
        case "resume":
            return workflow.resume(
                parse_nonblank_string(request, "change_id"),
                expected_revision=parse_expected_revision(request),
            )
        case "list_work":
            return workflow.list_work(parse_work_filter(request), expected_revision=parse_expected_revision(request))
        case "next_work":
            return workflow.next_work(expected_revision=parse_expected_revision(request))
        case "activate_work":
            return workflow.activate_work(parse_nonblank_string(request, "work_id"), expected_revision=parse_expected_revision(request))
        case "set_work_state":
            return workflow.set_work_state(parse_nonblank_string(request, "work_id"), parse_operational_state(request), parse_optional_string(request, "reason"), expected_revision=parse_expected_revision(request))
        case "add_work_relation":
            return workflow.add_work_relation(parse_nonblank_string(request, "predecessor_id"), parse_nonblank_string(request, "successor_id"), str(parse_work_relation_kind(request)), expected_revision=parse_expected_revision(request))
        case "remove_work_relation":
            return workflow.remove_work_relation(parse_nonblank_string(request, "predecessor_id"), parse_nonblank_string(request, "successor_id"), str(parse_work_relation_kind(request)), expected_revision=parse_expected_revision(request))
        case "reject":
            return workflow.reject(
                expected_revision=parse_expected_revision(request)
            )
        case "progress":
            return workflow.record_progress(
                parse_optional_string(request, "current_action"),
                parse_optional_string(request, "focus"),
                parse_strings(request, "completed_work"),
                parse_optional_string(request, "current_work"),
                parse_strings(request, "pending_work"),
                parse_optional_string(request, "resume_point"),
                expected_revision=parse_expected_revision(request),
            )
        case "accepted_task_progress":
            return workflow.accepted_task_progress(
                parse_nonblank_string(request, "repository_id"),
                parse_nonblank_string(request, "task_id"),
                parse_nonblank_string(request, "feature_agreement_ref"),
                parse_nonblank_string(request, "slice_agreement_ref"),
                parse_nonblank_string(request, "checkpoint"),
                parse_nonblank_string(request, "statement"),
                parse_nonblank_strings(request, "evidence_refs"),
                expected_revision=parse_expected_revision(request),
            )
        case "activate_provisional_slice_writes":
            return workflow.activate_provisional_slice_writes(
                parse_nonblank_string(request, "repository_id"),
                parse_nonblank_string(request, "feature_id"),
                expected_revision=parse_expected_revision(request),
            )
        case "propose_provisional_slice":
            return workflow.propose_provisional_slice(
                parse_nonblank_string(request, "repository_id"),
                parse_nonblank_string(request, "feature_id"),
                parse_provisional_slice_head(request),
                parse_nonblank_string(request, "title"),
                parse_nonblank_string(request, "outcome"),
                parse_optional_string(request, "example"),
                parse_provisional_slice_texts(request, "inclusions"),
                parse_provisional_slice_texts(request, "exclusions"),
                parse_provisional_slice_texts(request, "considerations"),
                parse_provisional_slice_texts(request, "open_questions"),
                parse_provisional_slice_criteria(request),
                expected_revision=parse_expected_revision(request),
            )
        case "criterion_test_result":
            return workflow.criterion_test_result(
                parse_nonblank_string(request, "repository_id"),
                parse_nonblank_string(request, "slice_agreement_ref"),
                parse_nonblank_string(request, "criteria_revision_id"),
                parse_nonblank_string(request, "criterion_id"),
                parse_nonblank_string(request, "test_kind"),
                parse_nonblank_string(request, "test_target"),
                parse_test_exit_code(request),
                parse_nonblank_string(request, "output_sha256"),
                expected_revision=parse_expected_revision(request),
            )
        case "criterion_test_result_r3b":
            return workflow.criterion_test_result_r3b(
                parse_nonblank_string(request, "repository_id"),
                parse_nonblank_string(request, "slice_agreement_ref"),
                parse_nonblank_string(request, "criteria_revision_id"),
                parse_nonblank_string(request, "criterion_id"),
                parse_nonblank_string(request, "test_kind"),
                parse_nonblank_string(request, "test_target"),
                parse_test_exit_code(request),
                parse_nonblank_string(request, "output_sha256"),
                parse_nonblank_string(request, "input_fingerprint_before"),
                parse_nonblank_string(request, "input_fingerprint_after"),
                parse_nonblank_string(request, "input_policy_id"),
                parse_nonblank_string(request, "input_policy_digest"),
                parse_nonblank_string(request, "admission_policy_id"),
                parse_nonblank_string(request, "admission_policy_digest"),
                parse_nonblank_string(request, "runtime_executable"),
                parse_nonblank_string(request, "runtime_executable_sha256"),
                parse_nonblank_string(request, "runtime_version"),
                parse_bounded_strings(request, "runtime_argv", maximum_items=16, maximum_length=2048),
                parse_nonblank_string(request, "environment_profile_id"),
                parse_nonblank_string(request, "environment_profile_digest"),
                parse_nonblank_string(request, "coverage_approval_id"),
                parse_nonblank_string(request, "coverage_contract_digest"),
                expected_revision=parse_expected_revision(request),
            )
        case "accept_slice_outcome":
            return workflow.accept_slice_outcome(
                parse_input_snapshot(request),
                expected_revision=parse_expected_revision(request),
            )
        case "accept_historical_slice_outcome":
            return workflow.accept_slice_outcome(
                parse_input_snapshot(request),
                expected_revision=parse_expected_revision(request),
                slice_agreement_ref=parse_nonblank_string(request, "slice_agreement_ref"),
            )
        case "assess_locks":
            return workflow.assess_locks(
                parse_lock_assessments(request),
                expected_revision=parse_expected_revision(request),
            )
        case "review":
            return workflow.record_review(
                parse_review_result(request),
                expected_revision=parse_expected_revision(request),
            )
        case "verify":
            return workflow.record_verification(
                parse_verification_evidence(request),
                expected_revision=parse_expected_revision(request),
            )
        case "converge":
            return workflow.converge(
                parse_convergence_assessment(request),
                expected_revision=parse_expected_revision(request),
            )
        case "invalidate_assurance":
            return workflow.invalidate_assurance(
                expected_revision=parse_expected_revision(request)
            )
        case "select_capability":
            return workflow.select_capability(
                parse_nonblank_string(request, "selection_id"),
                parse_nonblank_string(request, "choice"),
                expected_revision=parse_expected_revision(request),
            )
        case "recover_discovery":
            return workflow.recover_discovery(
                parse_nonblank_string(request, "recovery_id"),
                parse_nonblank_string(request, "candidate"),
                parse_nonblank_strings(request, "evidence_ids"),
                expected_revision=parse_expected_revision(request),
            )
        case "recover_legacy_non_material_detail":
            return workflow.recover_legacy_non_material_detail(
                parse_nonblank_string(request, "repository_id"),
                parse_nonblank_string(request, "feature_id"),
                parse_nonblank_string(request, "change_id"),
                parse_nonblank_string(request, "legacy_detail"),
                expected_revision=parse_expected_revision(request),
            )
        case "structure_material_clarification":
            choices = parse_nonblank_strings(request, "options")
            return workflow.structure_material_clarification(
                parse_nonblank_string(request, "repository_id"),
                parse_nonblank_string(request, "feature_id"),
                parse_nonblank_string(request, "change_id"),
                parse_nonblank_string(request, "legacy_detail"),
                parse_nonblank_string(request, "decision"), choices,
                expected_revision=parse_expected_revision(request),
            )
        case "authorize_irreversible":
            return workflow.authorize_irreversible(
                parse_irreversible_operation_kind(request),
                parse_nonblank_string(request, "target_ref"),
                parse_nonblank_string(request, "expected_remote_sha"),
                parse_git_history_rewrite_policy(request),
                parse_nonblank_string(request, "target_identity_name"),
                parse_nonblank_string(request, "target_identity_email"),
                parse_nonblank_string(request, "expected_result_sha"),
                parse_git_commit_rewrites(request),
                parse_nonblank_string(request, "purpose"),
                parse_boolean(request, "force_with_lease"),
                expected_revision=parse_expected_revision(request),
            )
        case "execute_irreversible":
            return workflow.execute_irreversible(
                parse_nonblank_string(request, "authorization_id"),
                expected_revision=parse_expected_revision(request),
            )
        case _:
            raise ProtocolError("unsupported action")

def main(argv: Sequence[str] | None = None) -> int:
    """Read one request from standard input and print one structured response."""
    values = tuple(sys.argv[1:] if argv is None else argv)
    if not values:
        root = Path(".")
    elif len(values) == 2 and values[0] == "--root":
        root = Path(values[1])
    else:
        raise ProtocolError("usage: evospec.agent_protocol [--root PATH]")
    try:
        payload = sys.stdin.buffer.read().decode("utf-8")
    except UnicodeDecodeError as error:
        response = encode_error("INVALID_REQUEST", str(error))
    else:
        response = execute(root, payload)
    sys.stdout.buffer.write((response + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
