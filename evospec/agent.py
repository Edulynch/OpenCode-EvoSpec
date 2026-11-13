"""Provider-neutral projection of authoritative workflow context."""

from __future__ import annotations

from dataclasses import dataclass

from .domain import AutonomyEnvelope, AutonomyEvaluation, AutonomyMetrics, IrreversibleAuthorization, IrreversibleAuthorizationSummary, OperationalState, PendingAutonomyExpansion, ReviewInspection, State, WorkItem
from .guidance import (
    ActionGuidance,
    MaterialDecisionEvidence,
    Outcome,
    OutcomeKind,
    allowed_actions,
    project,
)
from .workflow import ClarificationQuestion, WorkflowService

MAX_ACCEPTED_CAPABILITIES = 32
MAX_ACTIVE_LOCKS = 32
MAX_EXTERNAL_SDD_EVIDENCE = 8
MAX_WORK_ITEMS = 32
MAX_WORK_RELATIONS = 64
MAX_NEXT_CANDIDATES = MAX_WORK_ITEMS


@dataclass(frozen=True, slots=True)
class AgentProgress:
    current_action: str | None
    focus: str | None
    completed_work: tuple[str, ...]
    current_work: str | None
    pending_work: tuple[str, ...]
    resume_point: str | None


@dataclass(frozen=True, slots=True)
class AgentTaskProgress:
    """A current operational checkpoint, explicitly not an accepted verification."""

    task_id: str
    checkpoint: str
    statement: str
    report_kind: str
    source_kind: str
    source_reference: str | None
    evidence_refs: tuple[str, ...]
    recorded_revision: int


@dataclass(frozen=True, slots=True)
class AgentDecisionState:
    unresolved_material_decision: bool
    clarification_count: int
    constraint_count: int
    review_verdict: str | None
    verification_sufficient: bool


@dataclass(frozen=True, slots=True)
class AcceptedCapability:
    feature_id: str
    title: str
    capability: str
    living_spec_id: str
    publication_revision: str
    publication_path: str


@dataclass(frozen=True, slots=True)
class ActiveLockSummary:
    lock_id: str
    owner_feature_id: str
    lock_revision: int
    baseline_feature_id: str
    baseline_living_spec_id: str
    baseline_revision: str
    baseline_path: str
    protected_aspects: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExternalSDDSummary:
    framework: str
    paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DiscoveryRecoveryCandidate:
    candidate: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PendingDiscoveryRecoverySummary:
    recovery_id: str
    candidates: tuple[DiscoveryRecoveryCandidate, ...]


@dataclass(frozen=True, slots=True)
class PendingCapabilitySelectionSummary:
    selection_id: str
    choices: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkItemSummary:
    id: str
    operational_state: str
    feature_id: str | None
    change_id: str | None
    lifecycle: tuple[str, str] | None
    reason: str | None
    transitioned_at: str | None
    review_verdict: str | None
    verification_sufficient: bool
    activation_eligible: bool
    blocker: str | None


@dataclass(frozen=True, slots=True)
class WorkRelationSummary:
    predecessor_id: str
    successor_id: str
    kind: str


@dataclass(frozen=True, slots=True)
class WorkQueueSummary:
    all: tuple[WorkItemSummary, ...]
    pending: tuple[WorkItemSummary, ...]
    actionable: tuple[WorkItemSummary, ...]
    blocked: tuple[WorkItemSummary, ...]
    frozen: tuple[WorkItemSummary, ...]
    waiting: tuple[WorkItemSummary, ...]
    active: WorkItemSummary | None
    next: str | None
    next_code: str
    next_candidates: tuple[str, ...]
    next_candidates_total: int
    next_candidates_complete: bool
    relations: tuple[WorkRelationSummary, ...]
    total: int
    complete: bool
    relations_total: int
    relations_complete: bool


@dataclass(frozen=True, slots=True)
class AgentContext:
    revision: int
    initialization_state: str
    lifecycle: tuple[str, str] | None
    feature_id: str | None
    change_id: str | None
    classification: str | None
    progress: AgentProgress
    decision_state: AgentDecisionState
    side_work: str
    side_work_active: bool
    pending_intake: bool
    stale: bool
    stale_reason: str | None
    work_queue: WorkQueueSummary
    legal_actions: tuple[str, ...]
    guidance: tuple[ActionGuidance, ...]
    accepted_capabilities: tuple[AcceptedCapability, ...]
    accepted_capabilities_total: int
    accepted_capabilities_complete: bool
    active_locks: tuple[ActiveLockSummary, ...]
    active_locks_total: int
    active_locks_complete: bool
    external_sdd_evidence: tuple[ExternalSDDSummary, ...]
    external_sdd_evidence_total: int
    external_sdd_evidence_complete: bool
    questions: tuple[ClarificationQuestion, ...]
    completed_terminal: bool
    pending_discovery_recovery: PendingDiscoveryRecoverySummary | None
    pending_capability_selection: PendingCapabilitySelectionSummary | None
    outcome: Outcome | None
    autonomy_envelope: AutonomyEnvelope | None = None
    autonomy_metrics: AutonomyMetrics = AutonomyMetrics()
    autonomy_evaluation: AutonomyEvaluation | None = None
    irreversible_authorizations: tuple[IrreversibleAuthorizationSummary, ...] = ()
    pending_autonomy_expansion: PendingAutonomyExpansion | None = None
    repository_id: str | None = None
    accepted_slice: AcceptedSliceContext | None = None
    task_progress: AgentTaskProgress | None = None
    e1_schema_ready: bool | None = None


@dataclass(frozen=True, slots=True)
class AcceptedSliceContext:
    """One read-only projection of Core-owned accepted scope and initial Task."""

    feature_agreement_ref: str
    slice_agreement_ref: str
    feature_title: str
    slice_title: str
    task_id: str
    initial_task: str
    checkpoint: str
    source: str
    source_reference: str


def project_context(workflow: WorkflowService, inspection: ReviewInspection | None = None) -> AgentContext:
    """Return a concise, deterministic projection of current workflow authority."""
    if inspection is None:
        inspection = workflow.inspect_locks()
    e1_schema_ready = workflow.e1_schema_ready() if inspection.state.agreements else None
    queue_activation_available = (
        inspection.state.initialization is not None
        and workflow.queue_next(inspection.state).code in ("SELECTED", "AMBIGUOUS")
    )
    guidance = project(
        inspection, queue_activation_available=queue_activation_available, e1_schema_ready=e1_schema_ready,
    )
    if inspection.state.initialization is None:
        return AgentContext(
            inspection.state.revision,
            "UNINITIALIZED",
            None,
            None,
            None,
            None,
            AgentProgress(None, None, (), None, (), None),
            AgentDecisionState(False, 0, 0, None, False),
            "NONE",
            False,
            False,
            False,
            None,
            WorkQueueSummary((), (), (), (), (), (), None, None, "NONE", (), 0, True, (), 0, True, 0, True),
            allowed_actions(inspection, queue_activation_available=queue_activation_available, e1_schema_ready=e1_schema_ready),
            guidance,
            (),
            0,
            True,
            (),
            0,
            True,
            (),
            0,
            True,
             (),
             False,
             None,
             None,
              Outcome(OutcomeKind.STRUCTURAL_DEFECT, "repository authority initialization is required"),
        )
    external_sdd_discovery = workflow.external_sdd_evidence()
    external_sdd_evidence = tuple(
        ExternalSDDSummary(framework, paths)
        for framework, paths in external_sdd_discovery.evidence
    )
    accepted_capabilities = _accepted_capabilities(inspection)
    active_locks = _active_locks(inspection)
    accepted_capabilities_total = len(accepted_capabilities)
    active_locks_total = len(active_locks)
    work_queue = _work_queue(workflow, inspection)
    context = inspection.active_context
    if context is None:
        return AgentContext(
            inspection.state.revision,
            "INITIALIZED",
            None,
            None,
            None,
            None,
            AgentProgress(None, None, (), None, (), None),
            AgentDecisionState(False, 0, 0, None, False),
            "NONE",
            False,
            inspection.state.pending_intake is not None,
            False,
            None,
            work_queue,
            allowed_actions(inspection, queue_activation_available=queue_activation_available, e1_schema_ready=e1_schema_ready),
            guidance,
            accepted_capabilities[:MAX_ACCEPTED_CAPABILITIES],
            accepted_capabilities_total,
            accepted_capabilities_total <= MAX_ACCEPTED_CAPABILITIES,
            active_locks[:MAX_ACTIVE_LOCKS],
            active_locks_total,
            active_locks_total <= MAX_ACTIVE_LOCKS,
            external_sdd_evidence[:MAX_EXTERNAL_SDD_EVIDENCE],
            len(external_sdd_evidence),
            not external_sdd_discovery.limited and len(external_sdd_evidence) <= MAX_EXTERNAL_SDD_EVIDENCE,
             (),
             any(
                 archive.context.status.value == "ACCEPTED"
                 and archive.context.phase.value == "DONE"
                 for archive in inspection.state.terminal_archives
             ),
              _pending_discovery_recovery(inspection.state),
              _pending_capability_selection(inspection.state),
              _outcome(inspection, (), work_queue),
              irreversible_authorizations=_irreversible_authorization_summaries(inspection.state.irreversible_authorizations),
        )
    locks = tuple(lock for lock in inspection.locks if lock.active and lock.feature_id == context.feature_id)
    change = next((item for item in inspection.changes if item.id == context.change_id), None)
    current_assessments = () if change is None else change.lock_assessments
    stale = bool(locks) and (
        any(
            not any(
                assessment.lock_id == lock.id and assessment.lock_revision == lock.current_revision
                for assessment in current_assessments
            )
            for lock in locks
        )
    )
    questions = (
        workflow.clarification_questions(context)
        if context.change_id is not None and context.unresolved_material_decision
        else ()
    )
    return AgentContext(
        inspection.state.revision,
        "INITIALIZED",
        (str(context.status), str(context.phase)),
        str(context.feature_id),
        None if context.change_id is None else str(context.change_id),
        None if context.classification is None else str(context.classification),
        AgentProgress(
            context.current_action,
            context.focus,
            context.completed_work,
            context.current_work,
            context.pending_work,
            # resume_point remains a read-only compatibility projection, never a second E1 ledger.
            context.task_progress.checkpoint if context.task_progress is not None else context.resume_point,
        ),
        AgentDecisionState(
            context.unresolved_material_decision,
            len(context.decision_ids),
            len(context.constraint_ids),
            None if context.review is None else str(context.review.verdict),
            context.verification.sufficient,
        ),
        "SIDE" if context.side_of is not None else "PRIMARY",
        context.side_of is not None,
        inspection.state.pending_intake is not None,
        stale,
        "semantic lock assessment is missing or stale" if stale else None,
        work_queue,
        allowed_actions(inspection, queue_activation_available=queue_activation_available, e1_schema_ready=e1_schema_ready) +
        (("structure_material_clarification",) if context.safety_facts.unresolved_decision_details and context.unresolved_material_decision else ())
        + (("recover_legacy_non_material_detail",) if context.change_id is not None and context.safety_facts.unresolved_decision_details and context.unresolved_material_decision else ()),
        guidance,
        accepted_capabilities[:MAX_ACCEPTED_CAPABILITIES],
        accepted_capabilities_total,
        accepted_capabilities_total <= MAX_ACCEPTED_CAPABILITIES,
        active_locks[:MAX_ACTIVE_LOCKS],
        active_locks_total,
        active_locks_total <= MAX_ACTIVE_LOCKS,
        external_sdd_evidence[:MAX_EXTERNAL_SDD_EVIDENCE],
        len(external_sdd_evidence),
        not external_sdd_discovery.limited and len(external_sdd_evidence) <= MAX_EXTERNAL_SDD_EVIDENCE,
         questions,
         False,
           _pending_discovery_recovery(inspection.state),
           _pending_capability_selection(inspection.state),
            _outcome(inspection, questions, work_queue),
            context.autonomy_envelope,
            context.autonomy_metrics,
            workflow.autonomy_evaluation(inspection.state, "progress"),
            _irreversible_authorization_summaries(inspection.state.irreversible_authorizations),
            inspection.state.pending_autonomy_expansion,
            None if inspection.state.initialization is None else inspection.state.initialization.repository_id,
            _accepted_slice_context(inspection.state),
            None if context.task_progress is None else AgentTaskProgress(
                context.task_progress.task_id,
                context.task_progress.checkpoint,
                context.task_progress.statement,
                context.task_progress.report_kind,
                context.task_progress.source_kind,
                context.task_progress.source_reference,
                context.task_progress.evidence_refs,
                context.task_progress.recorded_revision,
            ),
            e1_schema_ready,
    )


def _accepted_slice_context(state: State) -> AcceptedSliceContext | None:
    context = state.active_context
    if context is None or context.slice_agreement_ref is None:
        return None
    feature = next((item for item in state.agreements if item.id == context.feature_agreement_ref), None)
    slice_record = next((item for item in state.agreements if item.id == context.slice_agreement_ref), None)
    if feature is None or slice_record is None:
        return None
    assert slice_record.task_id is not None
    assert slice_record.initial_task is not None
    assert slice_record.checkpoint is not None
    return AcceptedSliceContext(
        feature.id, slice_record.id, feature.title, slice_record.title,
        slice_record.task_id, slice_record.initial_task, slice_record.checkpoint,
        slice_record.source, slice_record.source_reference,
    )


def _irreversible_authorization_summaries(
    authorizations: tuple[IrreversibleAuthorization, ...],
) -> tuple[IrreversibleAuthorizationSummary, ...]:
    return tuple(
        IrreversibleAuthorizationSummary(
            str(item.id),
            item.operation_kind.value,
            str(item.feature_id),
            str(item.change_id),
            item.target_ref,
            item.expected_remote_sha,
            item.rewrite_policy.value,
            item.target_identity_name,
            item.target_identity_email,
            item.expected_result_sha,
            item.commit_rewrites,
            item.rewrite_plan_digest,
            item.purpose,
            item.force_with_lease,
            item.one_shot,
            item.authorized_revision,
            item.status.value,
            item.validated_revision,
            item.consumed_revision,
            item.resulting_remote_sha,
            item.invalidated_revision,
            item.invalidation_reason,
        )
        for item in authorizations
    )


def _pending_discovery_recovery(state: State) -> PendingDiscoveryRecoverySummary | None:
    recovery = state.pending_discovery_recovery
    if recovery is None:
        return None
    return PendingDiscoveryRecoverySummary(
        str(recovery.id),
        tuple(
            DiscoveryRecoveryCandidate(
                candidate,
                tuple(str(item.id) for item in recovery.evidence if item.candidate == candidate),
            )
            for candidate in recovery.candidates
        ),
    )


def _pending_capability_selection(state: State) -> PendingCapabilitySelectionSummary | None:
    selection = state.pending_capability_selection
    if selection is None:
        return None
    choices = tuple(str(item.feature_id) for item in selection.candidates)
    if selection.standalone_allowed:
        choices += ("CREATE_STANDALONE_CAPABILITY",)
    return PendingCapabilitySelectionSummary(str(selection.id), choices)


def _outcome(
    inspection: ReviewInspection,
    questions: tuple[ClarificationQuestion, ...],
    work_queue: WorkQueueSummary,
) -> Outcome | None:
    """Classify only current Core facts; actions remain separate compatibility guidance."""
    state = inspection.state
    if state.initialization is None:
        return Outcome(OutcomeKind.STRUCTURAL_DEFECT, "repository authority initialization is required")
    if state.pending_autonomy_expansion is not None:
        pending = state.pending_autonomy_expansion
        return Outcome(
            OutcomeKind.HUMAN_MATERIAL_DECISION,
            "an explicit decision is required for the exact pending autonomy envelope proposal",
            MaterialDecisionEvidence(
                f"resolve autonomy expansion proposal {pending.proposal_id}",
                ("AUTHORIZE", "REJECT", "NARROW"),
            ),
        )
    selection = _pending_capability_selection(state)
    if selection is not None:
        if len(selection.choices) < 2:
            return Outcome(OutcomeKind.STRUCTURAL_DEFECT, "pending capability selection lacks distinct choices")
        return Outcome(
            OutcomeKind.HUMAN_MATERIAL_DECISION,
            "a material capability decision is required",
            MaterialDecisionEvidence("select a capability", selection.choices),
        )
    context = inspection.active_context
    if context is not None and context.unresolved_material_decision:
        if questions and len(questions[0].options) >= 2 and len(set(questions[0].options)) == len(questions[0].options) and all(item.strip() for item in questions[0].options) and questions[0].prompt.strip():
            return Outcome(OutcomeKind.HUMAN_MATERIAL_DECISION,
                           "a Core-owned material clarification requires a choice",
                           MaterialDecisionEvidence(questions[0].prompt, questions[0].options))
        if questions:
            return Outcome(
                OutcomeKind.STRUCTURAL_DEFECT,
                "material clarification lacks Core-offered distinct choices",
            )
        return Outcome(OutcomeKind.STRUCTURAL_DEFECT, "material clarification facts are unavailable")
    if context is not None and context.status.value == "PAUSED":
        return Outcome(OutcomeKind.EXTERNAL_WAIT, "work is paused pending recoverable external progress")
    if work_queue.waiting:
        return Outcome(OutcomeKind.EXTERNAL_WAIT, "queued work is waiting on an external condition")
    if context is None and (
        state.terminal_archives
        or work_queue.all and all(item.operational_state == "DONE" for item in work_queue.all)
    ):
        return None
    return Outcome(OutcomeKind.SAFE_AUTOMATIC_OPERATION, "Core facts permit automatic progress")


def _accepted_capabilities(inspection: ReviewInspection) -> tuple[AcceptedCapability, ...]:
    """Project current published Feature authority from one inspection snapshot."""
    features = {item.id: item for item in inspection.features}
    capabilities = tuple(
        AcceptedCapability(
            str(feature_id),
            features[feature_id].title,
            publication.capability,
            str(publication.living_spec_id),
            publication.revision,
            str(publication.path),
        )
        for publication in inspection.publications
        if (feature_id := publication.feature_id) is not None and feature_id in features
    )
    return tuple(sorted(capabilities, key=lambda item: item.feature_id))


def _active_locks(inspection: ReviewInspection) -> tuple[ActiveLockSummary, ...]:
    """Project current active lock metadata without assessing its protected meaning."""
    revisions = {
        (item.lock_id, item.revision): item for item in inspection.lock_revisions
    }
    locks = tuple(
        ActiveLockSummary(
            str(lock.id),
            str(lock.feature_id),
            lock.current_revision,
            str(revisions[(lock.id, lock.current_revision)].baseline.feature_id),
            str(revisions[(lock.id, lock.current_revision)].baseline.living_spec_id),
            revisions[(lock.id, lock.current_revision)].baseline.revision,
            str(revisions[(lock.id, lock.current_revision)].baseline.path),
            revisions[(lock.id, lock.current_revision)].protected_aspects,
        )
        for lock in inspection.locks
        if lock.active
    )
    return tuple(sorted(locks, key=lambda item: (item.owner_feature_id, item.lock_id)))


def _work_queue(workflow: WorkflowService, inspection: ReviewInspection) -> WorkQueueSummary:
    state = inspection.state
    queued = tuple(sorted(state.work_items, key=lambda item: str(item.id)))
    summaries = tuple(_work_item_summary(workflow, state, item) for item in queued)
    active = None if state.active_context is None else WorkItemSummary(
        "" if state.active_work_id is None else str(state.active_work_id),
        "ACTIVE",
        str(state.active_context.feature_id),
        None if state.active_context.change_id is None else str(state.active_context.change_id),
        (str(state.active_context.status), str(state.active_context.phase)),
        None,
        None,
        None if state.active_context.review is None else str(state.active_context.review.verdict),
        state.active_context.verification.sufficient,
        False,
        None,
    )
    selected = workflow.queue_next(state)
    next_code = selected.code
    next_id = None if selected.work_id is None else str(selected.work_id)
    candidates = tuple(str(item) for item in selected.candidates)
    pending = tuple(item for item in summaries if item.operational_state != OperationalState.DONE)
    actionable = tuple(item for item in summaries if item.activation_eligible)
    relations = tuple(
        WorkRelationSummary(str(item.predecessor_id), str(item.successor_id), str(item.kind))
        for item in sorted(state.work_relations, key=lambda item: (str(item.predecessor_id), str(item.successor_id), str(item.kind)))
    )
    all_items = (() if active is None else (active,)) + summaries
    return WorkQueueSummary(
        all_items[:MAX_WORK_ITEMS], pending[:MAX_WORK_ITEMS], actionable[:MAX_WORK_ITEMS],
        tuple(item for item in summaries if item.operational_state == OperationalState.BLOCKED)[:MAX_WORK_ITEMS],
        tuple(item for item in summaries if item.operational_state == OperationalState.FROZEN)[:MAX_WORK_ITEMS],
        tuple(item for item in summaries if item.operational_state == OperationalState.WAITING)[:MAX_WORK_ITEMS],
        active, next_id, next_code, candidates[:MAX_NEXT_CANDIDATES], len(candidates),
        len(candidates) <= MAX_NEXT_CANDIDATES, relations[:MAX_WORK_RELATIONS],
        len(all_items), len(all_items) <= MAX_WORK_ITEMS, len(relations), len(relations) <= MAX_WORK_RELATIONS,
    )


def _work_item_summary(workflow: WorkflowService, state: State, item: WorkItem) -> WorkItemSummary:
    context = item.context
    blocker = workflow.queue_work_item_blocker(state, item.id) if item.operational_state is OperationalState.READY else None
    return WorkItemSummary(
        str(item.id), str(item.operational_state),
        None if context is None else str(context.feature_id),
        None if context is None or context.change_id is None else str(context.change_id),
        None if context is None else (str(context.status), str(context.phase)),
        item.reason, item.transitioned_at,
        None if context is None or context.review is None else str(context.review.verdict),
        False if context is None else context.verification.sufficient,
        item.operational_state is OperationalState.READY and blocker is None,
        blocker,
    )
