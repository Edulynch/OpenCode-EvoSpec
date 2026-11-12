"""Execute provider-neutral JSON requests over ``WorkflowService``."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .agent import AgentContext, WorkItemSummary, project_context
from .agent_protocol_schema import (
    ProtocolError,
    decode_request,
    parse_action,
    parse_autonomy_envelope_delta,
    parse_autonomy_expansion_decision,
    parse_boolean,
    parse_clarification_answers,
    parse_convergence_assessment,
    parse_expected_revision,
    parse_irreversible_operation_kind,
    parse_git_history_rewrite_policy,
    parse_git_commit_rewrites,
    parse_intent_assessment,
    parse_lock_assessments,
    parse_nonblank_string,
    parse_nonblank_strings,
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
from .domain import AssessmentOperation, AutonomyEnvelope, AutonomyEnvelopeDelta
from .persistence import JsonValue, RecoveryError
from .services import ServiceError, StaleRevisionError
from .workflow import (
    ClarificationAnswer,
    WorkflowError,
    WorkflowResult,
    WorkflowService,
)


def encode_success(result: WorkflowResult, context: AgentContext) -> str:
    return _encode({"result": _result_json(result), "context": context_json(context)})


def encode_error(code: str, detail: str) -> str:
    return _encode({"error": {"code": code, "detail": detail}})


def encode_stale_revision(detail: str, expected_revision: int, actual_revision: int) -> str:
    return _encode({"error": {"code": "STALE_REVISION", "detail": detail,
                                "expected_revision": expected_revision,
                                "actual_revision": actual_revision}})


def _encode(value: dict[str, JsonValue]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _result_json(result: WorkflowResult) -> dict[str, JsonValue]:
    return {"code": result.code, "revision": result.revision, "feature_id": result.feature_id,
            "change_id": result.change_id, "phase": result.phase, "message": result.message,
            "questions": [{"id": question.id, "prompt": question.prompt, "options": list(question.options)} for question in result.questions],
            "pending_intake_id": result.pending_intake_id, "focus": result.focus,
            "current_work": result.current_work, "pending_work": list(result.pending_work),
            "blocker": result.blocker, "classification": result.classification,
            "review_verdict": result.review_verdict, "verification_sufficient": result.verification_sufficient,
            "review_depth": result.review_depth, "verification_depth": result.verification_depth,
            "convergence_depth": result.convergence_depth}


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


def context_json(context: AgentContext) -> dict[str, JsonValue]:
    """Return the stable JSON-safe representation of an agent context."""
    payload: dict[str, JsonValue] = {"revision": context.revision, "repository_id": context.repository_id, "initialization_state": context.initialization_state,
            "lifecycle": None if context.lifecycle is None else list(context.lifecycle),
            "feature_id": context.feature_id, "change_id": context.change_id, "classification": context.classification,
            "progress": {"current_action": context.progress.current_action, "focus": context.progress.focus,
                          "completed_work": list(context.progress.completed_work), "current_work": context.progress.current_work,
                          "pending_work": list(context.progress.pending_work), "resume_point": context.progress.resume_point},
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
        workflow = WorkflowService.open(root)
        request = decode_request(payload)
        if parse_action(request) == "autonomy_evaluate":
            return _execute_autonomy_evaluation(workflow, request)
        result = _dispatch(workflow, request)
        return encode_success(result, project_context(workflow))
    except ProtocolError as error:
        return encode_error("INVALID_REQUEST", str(error))
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
    context = project_context(workflow)
    return _encode(
        {
            "evaluation": None
            if evaluation is None
            else {
                "authorization": evaluation.authorization.value,
                "reason": evaluation.reason,
            },
            "context": context_json(context),
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
