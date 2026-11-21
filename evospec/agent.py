"""Provider-neutral projection of authoritative workflow context."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from .domain import AgreementRecord, AutonomyEnvelope, AutonomyEvaluation, AutonomyMetrics, CriterionCoverageApproval, CriterionTestReceipt, ExpectedRevision, IrreversibleAuthorization, IrreversibleAuthorizationSummary, OperationalState, PendingAutonomyExpansion, ReviewInspection, SliceCriteriaHead, SliceCriteriaRevision, SliceCriterion, SliceOutcomeReceipt, State, WorkItem, WorkingContext
from .guidance import (
    ActionGuidance,
    MaterialDecisionEvidence,
    Outcome,
    OutcomeKind,
    allowed_actions,
    project,
)
from .input_fingerprint import InputSnapshot
from .request_scoped_objective import REQUEST_SCOPED_OBJECTIVE
from .services import RequestScopedObjectiveStatus
from .workflow import ClarificationQuestion, WorkflowError, WorkflowService

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
class SliceOutcomeSummary:
    status: str
    latest_receipt_id: str | None
    blockers: tuple[str, ...]
    receipt: SliceOutcomeReceipt | None = None


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
    accepted_slice: AcceptedSliceContext | None = None


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
class SliceAcceptanceItem:
    """One canonical Slice agreement, kept distinct from evaluation evidence."""

    slice_agreement_ref: str
    feature_id: str | None
    feature_agreement_ref: str | None
    feature_title: str | None
    slice_title: str | None
    authority_status: str
    scope_status: str
    provenance: str
    operational_state: str | None
    criteria_status: str
    criteria_revision: SliceCriteriaRevision | None
    historical_receipt: bool


@dataclass(frozen=True, slots=True)
class SliceAcceptanceInventory:
    """Bounded view of Core's canonical agreement records, not archive history."""

    source: str = "CORE_AGREEMENTS"
    total: int = 0
    complete: bool = True
    truncated: bool = False
    history_scope: str = "AGREEMENT_RECORDS_ONLY_NONEXHAUSTIVE"
    items: tuple[SliceAcceptanceItem, ...] = ()


@dataclass(frozen=True, slots=True)
class FeatureCompletionSummary:
    """Core-owned closure/readiness displayed without granting execution rights."""

    feature_id: str
    status: str
    receipt_id: str | None
    blockers: tuple[str, ...]
    slice_agreement_refs: tuple[str, ...]
    cycle_id: int = 0
    cycle_state: str = "OPEN"
    reopen_receipt_id: str | None = None
    history: tuple[tuple[int, str | None, str | None], ...] = ()


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
    criterion_test_receipts: tuple[CriterionTestReceipt, ...] = ()
    r3_schema_ready: bool = False
    r3b_schema_ready: bool = False
    input_snapshot: InputSnapshot | None = None
    slice_outcome: SliceOutcomeSummary | None = None
    criterion_coverage_approvals: tuple[CriterionCoverageApproval, ...] = ()
    slice_acceptance_inventory: SliceAcceptanceInventory = SliceAcceptanceInventory()
    feature_completion: FeatureCompletionSummary | None = None
    request_scoped_objective: RequestScopedObjectiveStatus | None = None


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
    criteria_status: str = "NOT_RECORDED"
    criteria_revision: SliceCriteriaRevision | None = None
    session_request: SessionRequestSnapshot | None = None


@dataclass(frozen=True, slots=True)
class SessionRequestSnapshot:
    """The immutable original request, distinct from TTY acceptance and tests."""

    statement: str
    sha256: str
    repository_id: str


def project_context(
    workflow: WorkflowService,
    inspection: ReviewInspection | None = None,
    input_snapshot: InputSnapshot | None = None,
) -> AgentContext:
    """Return a concise, deterministic projection of current workflow authority."""
    if inspection is None:
        inspection = workflow.inspect_locks()
    state = inspection.state
    slice_inventory = (
        _slice_acceptance_inventory(state)
        if state.initialization is not None else SliceAcceptanceInventory()
    )
    active_accepted_slice = _accepted_slice_context(state)
    active_session_request = bool(
        active_accepted_slice is not None
        and active_accepted_slice.session_request is not None
        and any(
            item.slice_agreement_ref == active_accepted_slice.slice_agreement_ref
            and item.authority_status == "CANONICAL"
            and item.scope_status == "ACTIVE"
            for item in slice_inventory.items
        )
    )
    r3b_schema_ready = state.r3b_schema_upgrade is not None
    request_scoped_objective = workflow.request_scoped_objective_status(
        state, input_snapshot,
    )
    if input_snapshot is None and (
        r3b_schema_ready or active_session_request
        or request_scoped_objective is not None
    ):
        try:
            _, input_snapshot = workflow.input_snapshot(
                expected_revision=ExpectedRevision(state.revision),
            )
        except WorkflowError:
            # A failed optional scan must not hide a committed workflow result.
            input_snapshot = None
        request_scoped_objective = workflow.request_scoped_objective_status(
            state, input_snapshot,
        )
    snapshot_unavailable = (
        r3b_schema_ready and input_snapshot is None
    )
    r3b_input_snapshot = input_snapshot if r3b_schema_ready else None
    completion = _feature_completion_summary(
        workflow, state, inspection.active_context,
        input_snapshot,
    )
    # A separate schema read could advertise a newer writer gate with an older revision.
    e1_schema_ready = inspection.e1_schema_ready
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
              r3_schema_ready=inspection.state.r3_schema_upgrade is not None,
              r3b_schema_ready=inspection.state.r3b_schema_upgrade is not None,
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
                r3_schema_ready=inspection.state.r3_schema_upgrade is not None,
                r3b_schema_ready=inspection.state.r3b_schema_upgrade is not None,
                input_snapshot=input_snapshot,
                repository_id=inspection.state.initialization.repository_id,
                criterion_coverage_approvals=(
                    () if r3b_input_snapshot is None else _current_cycle_coverage(
                        workflow, inspection.state, r3b_input_snapshot,
                    )
                ),
                 slice_acceptance_inventory=slice_inventory,
                 feature_completion=completion,
                 request_scoped_objective=request_scoped_objective,
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
    request_scoped_objective_slice = _has_request_scoped_objective_criteria(
        slice_inventory, context,
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
            active_accepted_slice,
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
             inspection.state.criterion_test_receipts,
             inspection.state.r3_schema_upgrade is not None,
             inspection.state.r3b_schema_upgrade is not None,
             input_snapshot,
               _slice_outcome_summary(
                    workflow,
                    inspection.state,
                    context,
                    input_snapshot if request_scoped_objective_slice else r3b_input_snapshot,
                    snapshot_unavailable,
                    allow_request_scoped_objective=request_scoped_objective_slice,
                ),
               criterion_coverage_approvals=(
                   ()
                   if r3b_input_snapshot is None
                   else _current_cycle_coverage(workflow, inspection.state, r3b_input_snapshot)
               ),
                slice_acceptance_inventory=slice_inventory,
                feature_completion=completion,
                request_scoped_objective=request_scoped_objective,
        )


def _feature_completion_summary(
    workflow: WorkflowService, state: State,
    context: WorkingContext | None, snapshot: InputSnapshot | None,
) -> FeatureCompletionSummary | None:
    """Project one unambiguous Feature; never pick work or issue a closure."""
    if state.initialization is None:
        return None
    primaries = tuple(sorted(
        (str(item.id) for item in state.features if item.side_of is None),
    ))
    selected = (
        str(context.feature_id) if context is not None and str(context.feature_id) in primaries
        else primaries[0] if len(primaries) == 1 else None
    )
    if selected is None:
        return None
    try:
        result = workflow.feature_completion_eligibility(state, selected, snapshot)
    except WorkflowError:
        return FeatureCompletionSummary(selected, "BLOCKED", None, ("AUTHORITY_UNAVAILABLE",), ())
    return FeatureCompletionSummary(
        selected, result.status, result.receipt_id, result.blockers, result.slice_agreement_refs,
        result.cycle_id, result.cycle_state, result.reopen_receipt_id, result.history,
    )


def _current_cycle_coverage(
    workflow: WorkflowService, state: State, snapshot: InputSnapshot,
) -> tuple[CriterionCoverageApproval, ...]:
    """Expose only fresh, Core-checked approvals; historical tests remain gated."""
    records = list(workflow.current_r3b_coverage_approvals(state, snapshot))
    if state.feature_reopen_receipts:
        for agreement in state.agreements:
            if agreement.kind != "SLICE":
                continue
            try:
                records.extend(workflow.current_r3b_coverage_approvals(
                    state, snapshot, agreement.id,
                ))
            except WorkflowError:
                # A closed or nonterminal Slice has no historical write authority.
                continue
    return tuple(dict((item.id, item) for item in records).values())


def _slice_acceptance_inventory(state: State) -> SliceAcceptanceInventory:
    """Project distinct Core Slice agreements without evaluating inactive work."""
    if not isinstance(state.agreements, tuple):
        return SliceAcceptanceInventory(complete=False)

    records_by_ref: dict[str, list[AgreementRecord]] = {}
    malformed_records = False
    for record in state.agreements:
        if not isinstance(record, AgreementRecord):
            malformed_records = True
            continue
        if record.kind not in ("FEATURE", "SLICE"):
            malformed_records = True
            continue
        if record.kind == "FEATURE":
            continue
        if not isinstance(record.id, str) or not record.id:
            malformed_records = True
            continue
        records_by_ref.setdefault(record.id, []).append(record)

    records = tuple(
        (reference, values[0], len(values) == 1 or all(value == values[0] for value in values))
        for reference, values in sorted(records_by_ref.items())
    )
    slice_refs = set(records_by_ref)
    malformed_records = malformed_records or (
        not isinstance(state.slice_criteria_heads, tuple)
        or not isinstance(state.slice_criteria_revisions, tuple)
        or any(
            not isinstance(head, SliceCriteriaHead)
            or head.slice_agreement_ref not in slice_refs
            for head in state.slice_criteria_heads
        )
        or any(
            not isinstance(revision, SliceCriteriaRevision)
            or revision.slice_agreement_ref not in slice_refs
            for revision in state.slice_criteria_revisions
        )
    )
    total = len(records)
    truncated = total > MAX_WORK_ITEMS
    selected = list(records)
    active_ref = (
        state.active_context.slice_agreement_ref
        if state.active_context is not None else None
    )
    if truncated and active_ref is not None:
        active_record = next((item for item in selected if item[0] == active_ref), None)
        if active_record is not None and active_record not in selected[:MAX_WORK_ITEMS]:
            selected = selected[:MAX_WORK_ITEMS - 1] + [active_record]
    selected = selected[:MAX_WORK_ITEMS]

    items: list[SliceAcceptanceItem] = []
    structurally_resolved = not malformed_records
    for _, record, unique in selected:
        item, resolved = _slice_acceptance_item(state, record, unique)
        items.append(item)
        structurally_resolved = structurally_resolved and resolved

    # Completeness covers the full agreement-record scope, not only the display cap.
    if truncated:
        for _, record, unique in records:
            if not unique:
                structurally_resolved = False
                continue
            _, resolved = _slice_acceptance_item(state, record, unique)
            structurally_resolved = structurally_resolved and resolved

    items.sort(key=lambda item: (item.feature_id or "\uffff", item.slice_agreement_ref))
    return SliceAcceptanceInventory(
        total=total,
        complete=structurally_resolved and not truncated,
        truncated=truncated,
        items=tuple(items),
    )


def _slice_acceptance_item(
    state: State, record: AgreementRecord, unique: bool,
) -> tuple[SliceAcceptanceItem, bool]:
    repository_id = (
        None if state.initialization is None else state.initialization.repository_id
    )
    feature_records = tuple(
        candidate for candidate in state.agreements
        if isinstance(candidate, AgreementRecord)
        and candidate.kind == "FEATURE"
        and candidate.repository_id == repository_id
        and candidate.feature_id == record.feature_id
        and candidate.change_id == record.change_id
    )
    feature = feature_records[0] if len(feature_records) == 1 else None
    association_valid = bool(
        unique
        and repository_id is not None
        and record.repository_id == repository_id
        and isinstance(record.feature_id, str) and record.feature_id
        and isinstance(record.change_id, str) and record.change_id
        and feature is not None
        and isinstance(feature.feature_id, str) and feature.feature_id
        and isinstance(feature.change_id, str) and feature.change_id
        and feature.task_id is None and feature.initial_task is None and feature.checkpoint is None
        and isinstance(record.title, str) and record.title.strip()
        and isinstance(record.statement, str) and record.statement.strip()
        and _agreement_sources_compatible(feature, record)
        and isinstance(record.source_reference, str) and record.source_reference.strip()
        and isinstance(record.task_id, str) and record.task_id.strip()
        and isinstance(record.initial_task, str) and record.initial_task.strip()
        and isinstance(record.checkpoint, str) and record.checkpoint.strip()
        and isinstance(feature.title, str) and feature.title.strip()
        and isinstance(feature.id, str) and feature.id
        and isinstance(feature.statement, str) and feature.statement.strip()
        and isinstance(feature.source_reference, str) and feature.source_reference.strip()
    )

    placements: list[tuple[str, str, str | None, WorkingContext]] = []

    def add(
        context: WorkingContext | None,
        provenance: str,
        status: str,
        queue_state: str | None = None,
    ) -> None:
        if context is not None and context.slice_agreement_ref == record.id:
            placements.append((provenance, status, queue_state, context))

    add(state.active_context, "ACTIVE_CONTEXT", "ACTIVE")
    add(state.preserved_primary_context, "PRESERVED_PRIMARY", "PRESERVED")
    if state.parked_work is not None:
        add(state.parked_work.context, "PARKED_CONTEXT", "PARKED")
    for work in state.work_items:
        if work.operational_state in (OperationalState.BLOCKED, OperationalState.FROZEN):
            add(work.context, "PARKED_WORK_ITEM", "PARKED", str(work.operational_state))
        else:
            add(work.context, "WORK_QUEUE", "QUEUED", str(work.operational_state))
        add(work.preserved_primary_context, "PRESERVED_WORK_ITEM", "PRESERVED")
    for archive in state.terminal_archives:
        add(archive.context, "TERMINAL_ARCHIVE", "HISTORICAL")

    provenance = "AGREEMENT_ONLY"
    scope_status = "INACTIVE"
    operational_state = None
    context_valid = True
    if len(placements) == 1:
        provenance, scope_status, operational_state, context = placements[0]
        context_valid = bool(
            feature is not None
            and context.feature_id == record.feature_id
            and context.change_id == record.change_id
            and context.feature_agreement_ref == feature.id
        )
    elif placements:
        provenance, scope_status, operational_state = "AMBIGUOUS_CONTEXT", "UNAVAILABLE", None
        context_valid = False

    criteria_status = "NOT_RECORDED"
    criteria_revision: SliceCriteriaRevision | None = None
    if not isinstance(state.slice_criteria_heads, tuple) or not isinstance(
        state.slice_criteria_revisions, tuple
    ):
        criteria_status = "UNAVAILABLE"
    else:
        raw_heads = tuple(
            head for head in state.slice_criteria_heads
            if getattr(head, "slice_agreement_ref", None) == record.id
        )
        raw_revisions = tuple(
            revision for revision in state.slice_criteria_revisions
            if getattr(revision, "slice_agreement_ref", None) == record.id
        )
        heads = tuple(
            head for head in state.slice_criteria_heads
            if isinstance(head, SliceCriteriaHead) and head.slice_agreement_ref == record.id
        )
        revisions = tuple(
            revision for revision in state.slice_criteria_revisions
            if isinstance(revision, SliceCriteriaRevision)
            and revision.slice_agreement_ref == record.id
        )
        if (
            any(not isinstance(head, SliceCriteriaHead) for head in raw_heads)
            or any(not isinstance(revision, SliceCriteriaRevision) for revision in raw_revisions)
            or len(heads) > 1
            or (not heads and revisions)
        ):
            criteria_status = "UNAVAILABLE"
        elif heads:
            matches = tuple(
                revision for revision in state.slice_criteria_revisions
                if isinstance(revision, SliceCriteriaRevision)
                and revision.id == heads[0].revision_id
            )
            if (
                len(matches) != 1
                or not isinstance(matches[0].id, str) or not matches[0].id
                or matches[0].slice_agreement_ref != record.id
                or matches[0].repository_id != record.repository_id
                or not isinstance(matches[0].outcome, str) or not matches[0].outcome.strip()
                or not isinstance(matches[0].example, str) or not matches[0].example.strip()
                or matches[0].source not in (
                    "INTERACTIVE_TERMINAL",
                    "INTERACTIVE_TERMINAL_DECISION",
                    REQUEST_SCOPED_OBJECTIVE,
                )
                or not isinstance(matches[0].source_reference, str) or not matches[0].source_reference.strip()
                or (
                    matches[0].source == REQUEST_SCOPED_OBJECTIVE
                    and (
                        record.source != "SESSION_REQUEST"
                        or matches[0].source_reference != record.source_reference
                    )
                )
                or any(
                    not isinstance(values, tuple)
                    or not values
                    or any(not isinstance(value, str) or not value.strip() for value in values)
                    for values in (
                        matches[0].inclusions, matches[0].exclusions, matches[0].decisions,
                    )
                )
                or not isinstance(matches[0].criteria, tuple)
                or not matches[0].criteria
                or len({
                    criterion.id for criterion in matches[0].criteria
                    if isinstance(criterion, SliceCriterion)
                }) != len(matches[0].criteria)
                or any(
                    not isinstance(criterion, SliceCriterion)
                    or not isinstance(criterion.id, str) or not criterion.id
                    or not isinstance(criterion.expected_result, str)
                    or not criterion.expected_result.strip()
                    or not isinstance(criterion.expected_evidence, str)
                    or not criterion.expected_evidence.strip()
                    for criterion in matches[0].criteria
                )
            ):
                criteria_status = "UNAVAILABLE"
            else:
                criteria_status = "RECORDED"
                criteria_revision = matches[0]

    valid_revision_ids = {
        revision.id for revision in state.slice_criteria_revisions
        if isinstance(revision, SliceCriteriaRevision)
        and revision.slice_agreement_ref == record.id
        and revision.repository_id == record.repository_id
        and isinstance(revision.id, str) and revision.id
    } if isinstance(state.slice_criteria_revisions, tuple) else set()
    historical_receipt = scope_status != "ACTIVE" and isinstance(
        state.slice_outcome_receipts, tuple
    ) and any(
        isinstance(receipt, SliceOutcomeReceipt)
        and receipt.slice_agreement_ref == record.id
        and receipt.repository_id == record.repository_id
        and receipt.criteria_revision_id in valid_revision_ids
        for receipt in state.slice_outcome_receipts
    )
    authority_valid = association_valid and context_valid and criteria_status != "UNAVAILABLE"
    item = SliceAcceptanceItem(
        record.id,
        str(record.feature_id) if association_valid else None,
        None if feature is None or not association_valid else feature.id,
        None if feature is None or not association_valid else feature.title,
        record.title if isinstance(record.title, str) and record.title.strip() else None,
        "CANONICAL" if association_valid and context_valid else "UNAVAILABLE",
        scope_status if association_valid and context_valid else "UNAVAILABLE",
        provenance if association_valid and context_valid else "ASSOCIATION_UNAVAILABLE",
        operational_state,
        criteria_status,
        criteria_revision,
        historical_receipt,
    )
    return item, authority_valid


def _accepted_slice_context(state: State) -> AcceptedSliceContext | None:
    return _accepted_slice_for_context(state, state.active_context)


def _has_request_scoped_objective_criteria(
    inventory: SliceAcceptanceInventory, context: WorkingContext,
) -> bool:
    return context.slice_agreement_ref is not None and any(
        item.slice_agreement_ref == context.slice_agreement_ref
        and item.feature_id == str(context.feature_id)
        and item.feature_agreement_ref == context.feature_agreement_ref
        and item.authority_status == "CANONICAL"
        and item.scope_status == "ACTIVE"
        and item.criteria_status == "RECORDED"
        and item.criteria_revision is not None
        and item.criteria_revision.source == REQUEST_SCOPED_OBJECTIVE
        for item in inventory.items
    )


def _slice_outcome_summary(
    workflow: WorkflowService,
    state: State,
    context: WorkingContext,
    snapshot: InputSnapshot | None,
    snapshot_unavailable: bool = False,
    *,
    allow_request_scoped_objective: bool = False,
) -> SliceOutcomeSummary | None:
    if context.slice_agreement_ref is None or (
        state.r3b_schema_upgrade is None and not allow_request_scoped_objective
    ):
        return None
    history = tuple(
        item for item in state.slice_outcome_receipts
        if item.slice_agreement_ref == context.slice_agreement_ref
    )
    latest_id = None if not history else history[-1].id
    latest_receipt = None if not history else history[-1]
    if snapshot is None:
        return SliceOutcomeSummary(
            "BLOCKED" if snapshot_unavailable else "REVALIDATION_REQUIRED",
            latest_id,
            ("INPUT_SNAPSHOT_UNAVAILABLE",) if snapshot_unavailable else (),
            latest_receipt,
        )
    eligibility = workflow.slice_outcome_eligibility(state, snapshot)
    canonical_receipt = next(
        (item for item in history if item.id == eligibility.latest_receipt_id),
        latest_receipt,
    )
    return SliceOutcomeSummary(
        eligibility.status, eligibility.latest_receipt_id, eligibility.blockers, canonical_receipt,
    )


def _accepted_slice_for_context(state: State, context: WorkingContext | None) -> AcceptedSliceContext | None:
    # A parked Task keeps its accepted agreement, but remains inactive. Project
    # its titles without promoting it to the current Task or selecting work.
    if context is None or context.slice_agreement_ref is None:
        return None
    feature = next((item for item in state.agreements if item.id == context.feature_agreement_ref), None)
    slice_record = next((item for item in state.agreements if item.id == context.slice_agreement_ref), None)
    if feature is None or slice_record is None:
        return None
    session_request = None
    if feature.source == "SESSION_REQUEST" or slice_record.source == "SESSION_REQUEST":
        repository_id = (
            None if state.initialization is None else state.initialization.repository_id
        )
        if (
            not _agreement_sources_compatible(feature, slice_record)
            or feature.kind != "FEATURE" or slice_record.kind != "SLICE"
            or repository_id is None
            or feature.repository_id != repository_id
            or slice_record.repository_id != repository_id
            or feature.feature_id != slice_record.feature_id
            or feature.change_id != slice_record.change_id
            or context.feature_id != slice_record.feature_id
            or context.change_id != slice_record.change_id
            or context.feature_agreement_ref != feature.id
        ):
            return None
        session_request = SessionRequestSnapshot(
            slice_record.statement,
            sha256(slice_record.statement.encode("utf-8")).hexdigest(),
            repository_id,
        )
    assert slice_record.task_id is not None
    assert slice_record.initial_task is not None
    assert slice_record.checkpoint is not None
    head = next((item for item in state.slice_criteria_heads if item.slice_agreement_ref == slice_record.id), None)
    revision = None if head is None else next(
        (item for item in state.slice_criteria_revisions if item.id == head.revision_id), None
    )
    return AcceptedSliceContext(
        feature.id, slice_record.id, feature.title, slice_record.title,
        slice_record.task_id, slice_record.initial_task, slice_record.checkpoint,
        slice_record.source, slice_record.source_reference,
        "NOT_RECORDED" if revision is None else "RECORDED", revision,
        session_request,
    )


def _agreement_sources_compatible(feature: AgreementRecord, slice_record: AgreementRecord) -> bool:
    if feature.source == "SESSION_REQUEST" or slice_record.source == "SESSION_REQUEST":
        return (
            feature.source == slice_record.source == "SESSION_REQUEST"
            and feature.source_reference == slice_record.source_reference
            and feature.statement == slice_record.statement
        )
    return (
        feature.source == slice_record.source == "INTERACTIVE_TERMINAL"
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
        _accepted_slice_for_context(state, context),
    )
