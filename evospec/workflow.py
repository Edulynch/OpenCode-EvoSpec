"""Agent-independent facade that coordinates policy with Core-owned authority."""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path

from .domain import (
    AssessmentConfidence,
    AssessmentDepth,
    AssessmentDepthValue,
    AssessmentEvidence,
    AssessmentOperation,
    AutonomyEnvelope,
    AutonomyEnvelopeDelta,
    AutonomyExpansionDecision,
    AutonomyExpansionId,
    AutonomyEvaluation,
    IrreversibleAuthorizationStatus,
    IrreversibleOperationKind,
    PendingCapabilitySelection,
    CapabilityCandidate,
    CapabilitySelectionId,
    DiscoveryRecoveryEvidence,
    PendingDiscoveryRecovery,
    CapabilityState,
    Change,
    ChangeId,
    ConvergenceAssessment,
    Decision,
    DecisionId,
    DeferredSideRequest,
    DeferredLinkedIntent,
    Drift,
    ExperimentOutcome,
    ExpectedRevision,
    Feature,
    FeatureId,
    GitCommitRewrite,
    GitHistoryRewritePolicy,
    IntakeId,
    IntentAssessment,
    MaterialDecision,
    MaterialClarification,
    validate_material_decision,
    LivingSpecId,
    LockAssessment,
    LockAuthorization,
    LockRelease,
    Observation,
    ObservationId,
    RepositoryInitialization,
    RepositoryPath,
    ReviewEvidence,
    ReviewEvidenceCategory,
    ReviewEvidenceKind,
    ReviewFinding,
    ReviewInspection,
    State,
    ReviewResult,
    SharedIntent,
    SharedIntentId,
    SemanticLock,
    SemanticLockRevision,
    SafetyFacts,
    StateValidationError,
    VerificationEvidence,
    WorkingContext,
    OperationalState,
    WorkId,
    WorkRelation,
    WorkRelationKind,
    inspect,
    validate_assessment_authority,
    validate_convergence_assessment_authority,
)
from .lifecycle import Classification, LifecycleState, Phase, ReviewVerdict
from .guidance import Action, for_action
from .discovery import (
    MAX_RECOVERY_ELIGIBLE_FILES,
    MAX_RECOVERY_ENTRIES,
    DiscoveryPolicy,
    ExternalSDDDiscovery,
    _IGNORED_PARTS,
    _NON_CAPABILITY_WORDS,
    _TEXT_SUFFIXES,
    _WORDS,
)
from .policies import AssurancePolicy, CapabilityRoute, IntakeOperation, IntakePolicy
from .services import CoreService, NextActionable, ServiceError, StaleRevisionError, UserAcceptance


class WorkflowError(Exception):
    """Raised when a facade request cannot safely be represented by Core."""


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    code: str
    revision: int
    feature_id: str | None
    change_id: str | None
    phase: str | None
    message: str
    questions: tuple[ClarificationQuestion, ...] = ()
    pending_intake_id: str | None = None
    focus: str | None = None
    current_work: str | None = None
    pending_work: tuple[str, ...] = ()
    blocker: str | None = None
    classification: str | None = None
    review_verdict: str | None = None
    verification_sufficient: bool | None = None
    review_depth: str | None = None
    verification_depth: str | None = None
    convergence_depth: str | None = None


InitResult = WorkflowResult
IntakeResult = WorkflowResult
ContinueResult = WorkflowResult
StatusResult = WorkflowResult
ReviewInspectionResult = WorkflowResult


@dataclass(frozen=True, slots=True)
class ClarificationAnswer:
    question_id: str
    answer: str


@dataclass(frozen=True, slots=True)
class ClarificationQuestion:
    """Stable material question derived from the current authoritative change."""

    id: str
    prompt: str
    options: tuple[str, ...] = ()


class WorkflowService:
    """The single workflow facade; all durable mutations delegate to CoreService."""

    __slots__ = ("__root", "__core")

    def __init__(self, root: Path, core: CoreService) -> None:
        self.__root = root
        self.__core = core

    @classmethod
    def open(cls, root: Path) -> WorkflowService:
        return cls(root, CoreService.open(root))

    def init(self, *, expected_revision: ExpectedRevision | None = None) -> InitResult:
        state, blocked = self._preflight(Action.INIT, expected_revision)
        if blocked is not None:
            return blocked
        identity = sha256(str(self.__root.resolve()).encode("utf-8")).hexdigest()[:16]
        evidence = DiscoveryPolicy(self.__root).discover()
        saved = self.__core.initialize(state, RepositoryInitialization(identity, evidence))
        return self._result("INITIALIZED", saved, "repository authority initialized")

    def accept_first_slice(
        self,
        feature_title: str,
        slice_title: str,
        initial_task: str,
        checkpoint: str,
        acceptance: UserAcceptance,
        *,
        expected_revision: ExpectedRevision,
    ) -> WorkflowResult:
        """Accept one human-approved Slice; never infer consent from lifecycle state."""
        state = self._mutation_state(expected_revision)
        try:
            saved = self.__core.accept_first_slice(
                state, feature_title, slice_title, initial_task, checkpoint, acceptance,
            )
        except ServiceError as error:
            raise WorkflowError(str(error)) from error
        return self._result("SLICE_ACCEPTED", saved, "accepted Feature, Slice and first Task persisted")

    def new(
        self,
        intent: str,
        assessment: IntentAssessment | None = None,
        *,
        expected_revision: ExpectedRevision | None = None,
    ) -> IntakeResult:
        return self._intake(IntakeOperation.NEW, intent, assessment, expected_revision)

    def change(
        self,
        intent: str,
        assessment: IntentAssessment | None = None,
        *,
        expected_revision: ExpectedRevision | None = None,
    ) -> IntakeResult:
        return self._intake(IntakeOperation.CHANGE, intent, assessment, expected_revision)

    def select_capability(
        self, selection_id: str, choice: str, *, expected_revision: ExpectedRevision
    ) -> WorkflowResult:
        state = self._mutation_state(expected_revision)
        pending = state.pending_capability_selection
        if pending is None or str(pending.id) != selection_id:
            return self._result("NO_PENDING_CAPABILITY_SELECTION", state, "capability selection is not pending")
        selected = next((item for item in pending.candidates if str(item.feature_id) == choice), None)
        try:
            saved = self.__core.select_capability(state, selection_id, choice)
        except ServiceError:
            return self._result("INVALID_CAPABILITY_SELECTION", state, "capability selection choice is not offered")
        if selected is not None:
            return self._result("ESTABLISHED_CAPABILITY", saved, "selected established capability requires explicit change intake")
        try:
            saved, needs_clarification = self._persist_safety_facts(
                saved,
                IntakePolicy(),
                Classification.FEATURE,
                (),
                None,
            )
        except ServiceError as error:
            raise WorkflowError("standalone capability safety facts could not be persisted") from error
        questions = self._questions_for_state(saved) if needs_clarification else ()
        return self._result("NEEDS_CLARIFICATION" if questions else "PROPOSED", saved, "selected standalone capability materialized", questions)

    def recover_discovery(
        self,
        recovery_id: str,
        candidate: str,
        evidence_ids: tuple[str, ...],
        *,
        expected_revision: ExpectedRevision,
    ) -> WorkflowResult:
        state = self._mutation_state(expected_revision)
        pending = state.pending_discovery_recovery
        if pending is None or str(pending.id) != recovery_id:
            return self._result("NO_PENDING_DISCOVERY_RECOVERY", state, "bounded discovery recovery is not pending")
        offered = tuple(item.id for item in pending.evidence if item.candidate == candidate)
        if candidate not in pending.candidates or tuple(evidence_ids) != offered:
            return self._result("INVALID_DISCOVERY_RECOVERY", state, "recovery evidence is not Core-offered")
        rebuilt = self._discovery_recovery(state, pending.intent)
        if rebuilt is None or replace(rebuilt, id=pending.id) != pending:
            return self._result("INVALID_DISCOVERY_RECOVERY", state, "bounded discovery evidence could not be revalidated")
        policy = IntakePolicy()
        native_candidates = tuple(
            (
                name,
                tuple(
                    f"project-native:recovery:{item.candidate}:{item.path}:{item.fingerprint}"
                    for item in pending.evidence
                    if item.candidate == name
                ),
            )
            for name in pending.candidates
        )
        route = policy.resolve_capability(state, pending.intent, native_candidates)
        selection: PendingCapabilitySelection | None = None
        if route.code == "AMBIGUOUS_CAPABILITY":
            candidates = tuple(
                CapabilityCandidate(feature.id, feature.title)
                for feature in state.features
                if DiscoveryPolicy.matches_capability(feature.title, pending.intent)
            )
            if len(candidates) < 2:
                return self._result("INVALID_DISCOVERY_RECOVERY", state, "recovery ambiguity is not Core-selectable")
            identifiers = ":".join(str(item.feature_id) for item in candidates)
            selection = PendingCapabilitySelection(
                CapabilitySelectionId(f"selection-{sha256(f'{AssessmentOperation.NEW}:{pending.intent}:{identifiers}'.encode()).hexdigest()[:12]}"),
                AssessmentOperation.NEW,
                pending.intent,
                candidates,
                True,
                tuple(f"feature:{item.feature_id}" for item in candidates),
            )
        try:
            saved = self.__core.resolve_discovery_recovery(state, recovery_id, selection)
        except ServiceError as error:
            raise WorkflowError("discovery recovery could not be persisted") from error
        if selection is not None:
            return self._result("AMBIGUOUS_CAPABILITY", saved, "multiple established capabilities require explicit selection")
        operation_result = self._operation_route_result(IntakeOperation.NEW, route)
        if operation_result is not None:
            return replace(self._result(operation_result, saved, "established capability requires explicit change intake"), feature_id=route.feature_id)
        return self._result("INVALID_DISCOVERY_RECOVERY", state, "recovery route is not established")

    def clarify(
        self,
        answers: tuple[ClarificationAnswer, ...] = (),
        *,
        expected_revision: ExpectedRevision | None = None,
    ) -> WorkflowResult:
        state, blocked = self._preflight(Action.CLARIFY, expected_revision)
        if blocked is not None:
            return blocked
        context = state.active_context
        if context is None or context.change_id is None:
            raise WorkflowError("no active work requires clarification")
        if not context.unresolved_material_decision:
            return self._result("READY", state, "no unresolved material clarification")
        questions = self.clarification_questions(context)
        if not questions or not questions[0].options:
            raise WorkflowError("legacy or malformed material clarification requires explicit structure")
        if not answers:
            return self._result("NEEDS_CLARIFICATION", state, "material decision remains unresolved", questions)
        question = questions[0]
        if len(answers) != 1 or answers[0].question_id != question.id or answers[0].answer not in question.options:
            raise WorkflowError("answer must select the exact pending Core option")
        remaining = context.safety_facts.material_clarifications[1:]
        try:
            state = self.__core.record_decision(
                state,
                Decision(DecisionId(f"decision-{question.id}"), context.feature_id,
                         "clarification", answers[0].answer, context.change_id),
            )
            current = state.active_context
            if current is None:
                raise WorkflowError("clarification context disappeared")
            state = self.__core.record_safety_facts(
                state,
                bool(remaining or current.safety_facts.unresolved_decision_details),
                replace(current.safety_facts, material_clarifications=remaining),
            )
            if not remaining and not current.safety_facts.unresolved_decision_details:
                state = self.__core.advance_lifecycle(state, LifecycleState.READY, Phase.READY)
        except ServiceError as error:
            raise WorkflowError("clarification could not be persisted") from error
        if remaining or context.safety_facts.unresolved_decision_details:
            return self._result(
                "NEEDS_CLARIFICATION",
                state,
                "material decisions remain unresolved",
                self.clarification_questions(state.active_context) if state.active_context is not None else (),
            )
        return self._result("READY", state, "material clarifications persisted")

    def structure_material_clarification(
        self, repository_id: str, feature_id: str, change_id: str, legacy_detail: str,
        decision: str, options: tuple[str, ...], *, expected_revision: ExpectedRevision,
    ) -> WorkflowResult:
        """Explicitly structure one exact legacy detail; never select its answer."""
        try:
            validate_material_decision(decision, options)
        except StateValidationError as error:
            raise WorkflowError(str(error)) from error
        state = self.__core.mutation_state(expected_revision)
        context = state.active_context
        if (state.initialization is None or repository_id != state.initialization.repository_id
                or context is None or str(context.feature_id) != feature_id
                or context.change_id is None or str(context.change_id) != change_id
                or not context.unresolved_material_decision
                or not context.safety_facts.unresolved_decision_details
                or context.safety_facts.unresolved_decision_details[0] != legacy_detail
                or context.safety_facts.unresolved_decision_details.count(legacy_detail) != 1):
            raise WorkflowError("recovery must match the active repository, Feature, Change and unresolved legacy detail")
        identifier = self._question(context.change_id, legacy_detail).id
        remaining = context.safety_facts.unresolved_decision_details[1:]
        try:
            saved = self.__core.record_safety_facts(
                state, True,
                replace(context.safety_facts, unresolved_decision_details=remaining,
                        material_clarifications=context.safety_facts.material_clarifications + (MaterialClarification(identifier, decision, options),)),
            )
        except ServiceError as error:
            raise WorkflowError("material clarification recovery could not be persisted") from error
        return self._result("NEEDS_CLARIFICATION", saved, "legacy clarification structured without resolution", self._questions_for_state(saved))

    def recover_legacy_non_material_detail(
        self, repository_id: str, feature_id: str, change_id: str, legacy_detail: str,
        *, expected_revision: ExpectedRevision,
    ) -> WorkflowResult:
        """Audit and remove one exact legacy detail without classifying it."""
        try:
            saved = self.__core.recover_legacy_non_material_detail(
                expected_revision, repository_id, feature_id, change_id, legacy_detail
            )
        except StaleRevisionError:
            raise
        except ServiceError as error:
            raise WorkflowError("legacy non-material recovery was rejected") from error
        context = saved.active_context
        if context is None:
            raise WorkflowError("legacy recovery context disappeared")
        if context.unresolved_material_decision:
            return self._result(
                "NEEDS_CLARIFICATION", saved,
                "legacy detail recorded as a non-material observation; material work remains",
                self._questions_for_state(saved),
            )
        return self._result(
            "READY", saved,
            "legacy detail recorded as a non-material observation; normal lifecycle resumed",
        )

    def status(self, state: State | None = None) -> StatusResult:
        state = self.__core.recover() if state is None else state
        return (
            self._init_required(state)
            if state.initialization is None
            else self._result("STATUS", state, "authoritative persisted status")
        )

    def clarification_questions(
        self, context: WorkingContext
    ) -> tuple[ClarificationQuestion, ...]:
        """Derive stable clarification questions from one persisted active context."""
        if context.change_id is None:
            raise WorkflowError("clarification context is unavailable")
        if context.safety_facts.material_clarifications:
            item = context.safety_facts.material_clarifications[0]
            return (ClarificationQuestion(item.id, item.decision, item.options),)
        return self._questions(context.change_id, context.safety_facts.unresolved_decision_details)[:1]

    def continue_work(
        self, *, expected_revision: ExpectedRevision | None = None
    ) -> ContinueResult:
        state, blocked = self._preflight(Action.CONTINUE, expected_revision)
        if blocked is not None:
            return blocked
        context = state.active_context
        if context is None:
            return self._result("NO_ACTIVE_WORK", state, "no active work")
        if context.status == LifecycleState.PAUSED:
            paused_pair = context.paused_pair
            if paused_pair is None:
                raise WorkflowError("paused context lacks a resumable lifecycle pair")
            paused_status, paused_phase = paused_pair
            try:
                state = self.__core.advance_lifecycle(state, paused_status, paused_phase)
            except (ServiceError, TypeError) as error:
                raise WorkflowError("paused work could not be resumed") from error
            return self._result("RESUMED", state, "paused context resumed exactly")
        if context.status in (LifecycleState.REJECTED, LifecycleState.ABANDONED, LifecycleState.ACCEPTED):
            return self._result("TERMINAL", state, "terminal work cannot reopen")
        return self._result("ACTIVE", state, "active context recovered without mutation")

    def park(
        self, reason: str, *, expected_revision: ExpectedRevision | None = None
    ) -> WorkflowResult:
        state, blocked = self._preflight(Action.PARK, expected_revision)
        if blocked is not None:
            return blocked
        try:
            saved = self.__core.park(state, reason)
        except ServiceError as error:
            raise WorkflowError("active work could not be parked") from error
        return self._result("PARKED", saved, "active work snapshot parked")

    def resume(
        self, work_id: str, *, expected_revision: ExpectedRevision | None = None
    ) -> WorkflowResult:
        state, blocked = self._preflight(Action.RESUME, expected_revision)
        if blocked is not None:
            return blocked
        resolved_work_id = next(
            (
                str(item.id)
                for item in state.work_items
                if str(item.id) == work_id
                or (item.context is not None and str(item.context.change_id) == work_id)
            ),
            work_id,
        )
        try:
            saved = self.__core.resume_parked(state, resolved_work_id)
        except ServiceError as error:
            raise WorkflowError("parked work could not be resumed") from error
        return self._result("RESUMED", saved, "parked work resumed exactly")

    def list_work(self, queue_filter: str, *, expected_revision: ExpectedRevision) -> WorkflowResult:
        state, blocked = self._preflight(Action.LIST_WORK, expected_revision)
        if blocked is not None:
            return blocked
        return self._result("WORK_LISTED", state, f"{queue_filter} work queue projected")

    def next_work(self, *, expected_revision: ExpectedRevision) -> WorkflowResult:
        state, blocked = self._preflight(Action.NEXT_WORK, expected_revision)
        if blocked is not None:
            return blocked
        projection = self.__core.next_actionable(state)
        return self._result(projection.code, state, "next work selection projected")

    def queue_next(self, state: State) -> NextActionable:
        return self.__core.next_actionable(state)

    def autonomy_evaluation(
        self,
        state: State,
        action: str,
        paths: tuple[RepositoryPath, ...] = (),
        *,
        irreversible: bool = False,
    ) -> AutonomyEvaluation | None:
        if state.active_context is None:
            return None
        return self.__core.evaluate_autonomy(state, action, paths, irreversible=irreversible)

    def propose_autonomy_expansion(
        self,
        delta: AutonomyEnvelopeDelta,
        *,
        expected_revision: ExpectedRevision,
    ) -> WorkflowResult:
        state, blocked = self._preflight(Action.PROPOSE_AUTONOMY_EXPANSION, expected_revision)
        if blocked is not None:
            return blocked
        try:
            saved = self.__core.propose_autonomy_expansion(state, delta)
        except ServiceError as error:
            raise WorkflowError("autonomy expansion proposal was rejected by Core") from error
        return self._result(
            "AUTONOMY_EXPANSION_PROPOSED",
            saved,
            "exact autonomy envelope delta awaits an explicit human decision",
        )

    def resolve_autonomy_expansion(
        self,
        proposal_id: str,
        decision: AutonomyExpansionDecision,
        narrowed_envelope: AutonomyEnvelope | None = None,
        *,
        expected_revision: ExpectedRevision,
    ) -> WorkflowResult:
        state, blocked = self._preflight(Action.RESOLVE_AUTONOMY_EXPANSION, expected_revision)
        if blocked is not None:
            return blocked
        try:
            saved = self.__core.resolve_autonomy_expansion(
                state,
                AutonomyExpansionId(proposal_id),
                decision,
                narrowed_envelope,
            )
        except ServiceError as error:
            raise WorkflowError("autonomy expansion decision was rejected by Core") from error
        code = {
            AutonomyExpansionDecision.AUTHORIZE: "AUTONOMY_EXPANSION_AUTHORIZED",
            AutonomyExpansionDecision.REJECT: "AUTONOMY_EXPANSION_REJECTED",
            AutonomyExpansionDecision.NARROW: "AUTONOMY_EXPANSION_NARROWED",
        }[decision]
        return self._result(code, saved, "explicit autonomy envelope decision recorded")

    def authorize_irreversible(
        self,
        operation_kind: IrreversibleOperationKind,
        target_ref: str,
        expected_remote_sha: str,
        rewrite_policy: GitHistoryRewritePolicy,
        target_identity_name: str,
        target_identity_email: str,
        expected_result_sha: str,
        commit_rewrites: tuple[GitCommitRewrite, ...],
        purpose: str,
        force_with_lease: bool,
        *,
        expected_revision: ExpectedRevision,
    ) -> WorkflowResult:
        state, blocked = self._preflight(Action.AUTHORIZE_IRREVERSIBLE, expected_revision)
        if blocked is not None:
            return blocked
        try:
            saved = self.__core.record_irreversible_authorization(
                state,
                operation_kind,
                target_ref,
                expected_remote_sha,
                rewrite_policy,
                target_identity_name,
                target_identity_email,
                expected_result_sha,
                commit_rewrites,
                purpose,
                force_with_lease,
            )
        except ServiceError as error:
            raise WorkflowError("irreversible authorization was rejected by Core") from error
        return self._result(
            "IRREVERSIBLE_AUTHORIZED", saved, "one-shot irreversible authorization recorded"
        )

    def execute_irreversible(
        self, authorization_id: str, *, expected_revision: ExpectedRevision
    ) -> WorkflowResult:
        state, blocked = self._preflight(Action.EXECUTE_IRREVERSIBLE, expected_revision)
        if blocked is not None:
            return blocked
        try:
            saved, unaffected_refs_unchanged = self.__core.execute_irreversible_authorization(
                state, authorization_id
            )
        except ServiceError as error:
            raise WorkflowError("Core could not execute the exact authorized Git operation") from error
        authorization = next(
            item for item in saved.irreversible_authorizations if str(item.id) == authorization_id
        )
        if authorization.status is IrreversibleAuthorizationStatus.INVALIDATED:
            return self._result(
                "IRREVERSIBLE_INVALIDATED",
                saved,
                authorization.invalidation_reason or "authorization became stale before Git execution",
            )
        if authorization.status is not IrreversibleAuthorizationStatus.CONSUMED:
            raise WorkflowError("Core returned without consuming the completed Git operation")
        return self._result(
            "IRREVERSIBLE_CONSUMED",
            saved,
            "Core executed the exact force-with-lease Git push and consumed the one-shot authorization"
            if unaffected_refs_unchanged
            else "the authorized target was pushed and consumed; inspect concurrent changes to other remote refs",
        )

    def queue_work_item_blocker(self, state: State, work_id: WorkId) -> str | None:
        return self.__core.work_item_blocker(state, work_id)

    def activate_work(self, work_id: str, *, expected_revision: ExpectedRevision) -> WorkflowResult:
        state = self._queue_state(expected_revision)
        guidance = for_action(
            inspect(state),
            Action.ACTIVATE_WORK,
            queue_activation_available=self.__core.next_actionable(state).code in ("SELECTED", "AMBIGUOUS"),
        )
        if not guidance.allowed:
            raise WorkflowError(guidance.message)
        try:
            saved = self.__core.activate_work(state, work_id)
        except ServiceError as error:
            raise WorkflowError("work item could not be activated") from error
        return self._result("ACTIVATED", saved, "work item activated exactly")

    def set_work_state(
        self, work_id: str, operational_state: OperationalState, reason: str | None,
        *, expected_revision: ExpectedRevision,
    ) -> WorkflowResult:
        state, blocked = self._preflight(Action.SET_WORK_STATE, expected_revision)
        if blocked is not None:
            return blocked
        try:
            saved = self.__core.set_work_state(state, WorkId(work_id), operational_state, reason)
        except (ServiceError, ValueError) as error:
            raise WorkflowError("work item state could not be updated") from error
        return self._result("WORK_STATE_SET", saved, "work item state updated")

    def add_work_relation(
        self, predecessor_id: str, successor_id: str, kind: str, *, expected_revision: ExpectedRevision,
    ) -> WorkflowResult:
        state, blocked = self._preflight(Action.ADD_WORK_RELATION, expected_revision)
        if blocked is not None:
            return blocked
        try:
            saved = self.__core.add_work_relation(state, WorkRelation(WorkId(predecessor_id), WorkId(successor_id), WorkRelationKind(kind)))
        except (ServiceError, ValueError) as error:
            raise WorkflowError("work relation could not be added") from error
        return self._result("WORK_RELATION_ADDED", saved, "work relation added")

    def remove_work_relation(
        self, predecessor_id: str, successor_id: str, kind: str, *, expected_revision: ExpectedRevision,
    ) -> WorkflowResult:
        state, blocked = self._preflight(Action.REMOVE_WORK_RELATION, expected_revision)
        if blocked is not None:
            return blocked
        try:
            saved = self.__core.remove_work_relation(state, WorkRelation(WorkId(predecessor_id), WorkId(successor_id), WorkRelationKind(kind)))
        except (ServiceError, ValueError) as error:
            raise WorkflowError("work relation could not be removed") from error
        return self._result("WORK_RELATION_REMOVED", saved, "work relation removed")

    def _queue_state(self, expected_revision: ExpectedRevision) -> State:
        state = self.__core.recover()
        if state.revision != expected_revision:
            raise StaleRevisionError(expected_revision, state.revision)
        return state

    def review(self) -> ReviewInspectionResult:
        return self._result("REVIEW", self.__core.recover(), "read-only durable review inspection")

    def inspect_locks(self) -> ReviewInspection:
        """Expose the authoritative lock projection without a lifecycle mutation."""
        return self.__core.inspect_review()

    def external_sdd_evidence(self) -> ExternalSDDDiscovery:
        """Expose bounded foreign SDD structure without persisting or interpreting it."""
        return DiscoveryPolicy(self.__root).external_sdd_evidence()

    def create_lock(self, lock: SemanticLock, revision: SemanticLockRevision) -> WorkflowResult:
        state = self.__core.recover()
        try:
            saved = self.__core.create_lock(state, lock, revision)
        except ServiceError as error:
            raise WorkflowError("semantic lock could not be created") from error
        return self._result("LOCK_CREATED", saved, "semantic lock persisted")

    def revise_lock(self, revision: SemanticLockRevision) -> WorkflowResult:
        state = self.__core.recover()
        try:
            saved = self.__core.revise_lock(state, revision)
        except ServiceError as error:
            raise WorkflowError("semantic lock could not be revised") from error
        return self._result("LOCK_REVISED", saved, "semantic lock revision persisted")

    def retire_lock(self, release: LockRelease) -> WorkflowResult:
        state = self.__core.recover()
        try:
            saved = self.__core.retire_lock(state, release)
        except ServiceError as error:
            raise WorkflowError("semantic lock could not be retired") from error
        return self._result("LOCK_RETIRED", saved, "semantic lock retirement persisted")

    def assess_locks(
        self,
        assessments: tuple[LockAssessment, ...],
        *,
        expected_revision: ExpectedRevision | None = None,
    ) -> WorkflowResult:
        state, blocked = self._preflight(Action.ASSESS_LOCKS, expected_revision)
        if blocked is not None:
            return blocked
        context = state.active_context
        if context is None or context.change_id is None:
            raise WorkflowError("no active Change for lock assessment")
        try:
            saved = self.__core.assess_locks(state, context.change_id, assessments)
        except ServiceError as error:
            raise WorkflowError("semantic lock assessment could not be persisted") from error
        return self._result("LOCKS_ASSESSED", saved, "semantic lock assessment persisted")

    def authorize_lock(self, authorization: LockAuthorization) -> WorkflowResult:
        state = self.__core.recover()
        try:
            saved = self.__core.authorize_lock(state, authorization)
        except ServiceError as error:
            raise WorkflowError("semantic lock authorization could not be persisted") from error
        return self._result("LOCK_AUTHORIZED", saved, "semantic lock authorization persisted")

    def record_lock_decision(self, decision: Decision) -> WorkflowResult:
        """Record the explicitly scoped decision required before authorizing a lock conflict."""
        state = self.__core.recover()
        try:
            saved = self.__core.record_decision(state, decision)
        except ServiceError as error:
            raise WorkflowError("semantic lock decision could not be persisted") from error
        return self._result("LOCK_DECISION_RECORDED", saved, "semantic lock decision persisted")

    def pause(self) -> WorkflowResult:
        state = self.__core.recover()
        context = state.active_context
        if context is None:
            raise WorkflowError("no active work to pause")
        saved = self.__core.advance_lifecycle(state, LifecycleState.PAUSED, context.phase)
        return self._result("PAUSED", saved, "active context paused")

    def record_progress(
        self,
        current_action: str | None,
        focus: str | None,
        completed_work: tuple[str, ...],
        current_work: str | None,
        pending_work: tuple[str, ...],
        resume_point: str | None,
        *,
        expected_revision: ExpectedRevision | None = None,
    ) -> WorkflowResult:
        state, blocked = self._preflight(Action.PROGRESS, expected_revision)
        if blocked is not None:
            return blocked
        context = state.active_context
        if context is None:
            raise WorkflowError("no active work for progress")
        if context.status == LifecycleState.PROPOSED:
            state = self.__core.advance_lifecycle(state, LifecycleState.READY, Phase.READY)
            state = self.__core.advance_lifecycle(state, LifecycleState.IN_PROGRESS, Phase.IMPLEMENT)
        elif context.status == LifecycleState.READY:
            state = self.__core.advance_lifecycle(state, LifecycleState.IN_PROGRESS, Phase.IMPLEMENT)
        saved = self.__core.record_progress(state, current_action, focus, completed_work, current_work, pending_work, resume_point)
        return self._result("IN_PROGRESS", saved, "implementation progress persisted")

    def record_review(
        self,
        review: ReviewResult,
        *,
        expected_revision: ExpectedRevision | None = None,
    ) -> WorkflowResult:
        state, blocked = self._preflight(Action.REVIEW, expected_revision)
        if blocked is not None:
            return blocked
        context = state.active_context
        if context is None:
            raise WorkflowError("no active work for review")
        if context.phase == Phase.IMPLEMENT:
            state = self.__core.advance_lifecycle(state, LifecycleState.IN_PROGRESS, Phase.REVIEW)
        current = state.active_context
        if current is None:
            raise WorkflowError("review work disappeared")
        evidence = self._review_evidence(current, state)
        findings = tuple(
            ReviewFinding(
                review.verdict,
                ReviewEvidenceCategory.WORKFLOW,
                finding,
                "caller supplied review finding",
            )
            for finding in review.findings
        )
        if review.verdict != ReviewVerdict.PASS and not findings:
            findings = (
                ReviewFinding(
                    review.verdict,
                    ReviewEvidenceCategory.WORKFLOW,
                    str(review.verdict),
                    "caller supplied review verdict",
                ),
            )
        lock_gate = self.__core.lock_gate(state)
        if lock_gate is not None:
            findings += (
                ReviewFinding(
                    ReviewVerdict.BLOCKED,
                    ReviewEvidenceCategory.CONSTRAINT,
                    lock_gate,
                    "Core lock relationships require an assessment or exact authorization",
                ),
            )
        evaluated = AssurancePolicy().evaluate_records(evidence, findings)
        saved = self.__core.record_review(state, evaluated, evidence=evidence, findings=findings)
        return self._result("REVIEWED", saved, "review persisted")

    def record_verification(
        self,
        verification: VerificationEvidence,
        *,
        expected_revision: ExpectedRevision | None = None,
    ) -> WorkflowResult:
        state, blocked = self._preflight(Action.VERIFY, expected_revision)
        if blocked is not None:
            return blocked
        context = state.active_context
        if context is None:
            raise WorkflowError("no active work for verification")
        if context.phase == Phase.REVIEW:
            state = self.__core.advance_lifecycle(state, LifecycleState.VERIFYING, Phase.VERIFY)
        required_evidence = 2 if context.safety_facts.verification_depth == "FULL" else 1
        evaluated = VerificationEvidence(
            verification.evidence,
            verification.sufficient and len(verification.evidence) >= required_evidence,
        )
        saved = self.__core.record_verification(state, evaluated)
        return self._result("VERIFIED" if evaluated.sufficient else "INSUFFICIENT", saved, "verification persisted")

    def invalidate_assurance(
        self, *, expected_revision: ExpectedRevision | None = None
    ) -> WorkflowResult:
        """Reset review-derived assurance after implementation changes."""
        state, blocked = self._preflight(Action.INVALIDATE_ASSURANCE, expected_revision)
        if blocked is not None:
            return blocked
        saved = self.__core.record_implementation_alteration(state)
        return self._result("ASSURANCE_INVALIDATED", saved, "assurance invalidated")

    def record_experiment(self, statement: str, outcome: ExperimentOutcome) -> WorkflowResult:
        """Persist an explicit experiment result without publishing product truth."""
        state = self.__core.recover()
        context = state.active_context
        if context is None or context.change_id is None:
            raise WorkflowError("no active work for experiment evidence")
        if not statement.strip():
            raise WorkflowError("experiment statement must not be empty")
        observation_id = sha256(
            f"{context.change_id}:experiment:{outcome}:{statement.strip()}".encode("utf-8")
        ).hexdigest()[:12]
        try:
            saved = self.__core.record_observation(
                state,
                Observation(
                    ObservationId(f"experiment-{observation_id}"),
                    context.change_id,
                    "experiment",
                    statement.strip(),
                    outcome == ExperimentOutcome.ACCEPTED,
                    outcome,
                ),
            )
        except ServiceError as error:
            raise WorkflowError("experiment outcome could not be persisted") from error
        return self._result("EXPERIMENT_RECORDED", saved, "explicit experiment outcome persisted")

    def reconcile_drift(self, drift: Drift, decision: Decision) -> WorkflowResult:
        """Persist an observed divergence and its explicit product decision through Core."""
        state = self.__core.recover()
        context = state.active_context
        if (
            context is None
            or context.change_id is None
            or drift.change_id != context.change_id
            or decision.feature_id != context.feature_id
            or decision.change_id != context.change_id
            or decision.kind != "constraint"
        ):
            raise WorkflowError("reconciliation decision must belong to active work")
        try:
            state = self.__core.record_decision(state, decision)
            saved = self.__core.record_drift(
                state,
                replace(drift, change_id=context.change_id, unresolved=False),
            )
        except ServiceError as error:
            raise WorkflowError("drift reconciliation could not be persisted") from error
        return self._result("RECONCILED", saved, "explicit drift reconciliation persisted")

    def converge(
        self,
        assessment: ConvergenceAssessment,
        *,
        expected_revision: ExpectedRevision | None = None,
    ) -> WorkflowResult:
        state, blocked = self._preflight(Action.CONVERGE, expected_revision)
        if blocked is not None:
            return blocked
        context = state.active_context
        if context is None:
            raise WorkflowError("no active work for convergence")
        if (
            type(assessment) is not ConvergenceAssessment
            or context.change_id is None
            or assessment.change_id != context.change_id
            or type(assessment.product_truth_changed) is not bool
        ):
            raise WorkflowError("convergence requires an assessment for the active Change")
        try:
            validate_convergence_assessment_authority(state, assessment)
        except StateValidationError as error:
            raise WorkflowError("convergence assessment is invalid") from error
        if context.change_id is not None:
            experiments = tuple(
                item
                for item in state.observations
                if item.change_id == context.change_id and item.kind == "experiment"
            )
            if context.classification == Classification.EXPERIMENT and not experiments:
                raise WorkflowError("experiment convergence requires an explicit outcome")
            if any(item.outcome == ExperimentOutcome.REJECTED for item in experiments):
                raise WorkflowError("rejected experiment cannot publish product truth")
            unresolved = tuple(
                item
                for item in state.drifts
                if item.unresolved and item.change_id == context.change_id
            )
            if unresolved and not any(
                item.kind == "constraint" and item.change_id == context.change_id
                for item in state.decisions
            ):
                raise WorkflowError("unresolved drift requires explicit reconciliation")
        if context.phase == Phase.VERIFY:
            state = self.__core.advance_lifecycle(state, LifecycleState.VERIFYING, Phase.CONVERGE)
        state = self.__core.confirm_convergence(state)
        terminal = state.active_context
        if terminal is None:
            raise WorkflowError("convergence authority disappeared")
        try:
            if assessment.product_truth_changed and terminal.side_of is None:
                primary_change_id = terminal.change_id
                if primary_change_id is None:
                    raise WorkflowError("primary convergence requires a terminal change")
                content = assessment.product_truth_content
                if content is None:
                    raise WorkflowError("changed Product Truth requires explicit publication content")
                saved = self.__core.publish_accepted_convergence(
                    state,
                    terminal.feature_id,
                    primary_change_id,
                    LivingSpecId(f"spec-{terminal.feature_id}"),
                    str(state.revision + 1),
                    content.value,
                    assessment,
                )
            elif assessment.product_truth_changed:
                content = assessment.product_truth_content
                if content is None:
                    raise WorkflowError("changed Product Truth requires explicit publication content")
                saved = self.__core.publish_accepted_side_convergence(
                    state,
                    str(state.revision + 1),
                    content.value,
                    assessment,
                )
            else:
                saved = self.__core.accept_convergence_without_publication(state, assessment)
        except (ServiceError, TypeError) as error:
            raise WorkflowError("convergence is guarded by review and verification") from error
        return self._result("DONE", saved, "accepted terminal archive persisted")

    def reject(
        self, *, expected_revision: ExpectedRevision | None = None
    ) -> WorkflowResult:
        """Explicitly complete current work as rejected without reopening it."""
        state, blocked = self._preflight(Action.REJECT, expected_revision)
        if blocked is not None:
            return blocked
        context = state.active_context
        if context is None:
            raise WorkflowError("no active work to reject")
        try:
            if context.status == LifecycleState.PROPOSED:
                state = self.__core.advance_lifecycle(state, LifecycleState.READY, Phase.READY)
                context = state.active_context
            if context is None:
                raise WorkflowError("active work disappeared")
            terminal_status = (
                LifecycleState.REJECTED
                if context.phase in (Phase.VERIFY, Phase.CONVERGE)
                else LifecycleState.ABANDONED
            )
            terminal = replace(context, status=terminal_status, phase=context.phase)
            saved = self.__core.complete_side(state, terminal) if context.side_of is not None else self.__core.archive_terminal(state, terminal)
        except ServiceError as error:
            raise WorkflowError("terminal outcome could not be persisted") from error
        code = "PRIMARY_RESTORED" if context.side_of is not None else "REJECTED"
        return self._result(code, saved, "explicit terminal outcome persisted")

    def _intake(
        self,
        operation: IntakeOperation,
        intent: str,
        assessment: IntentAssessment | None,
        expected_revision: ExpectedRevision | None,
    ) -> IntakeResult:
        if not intent.strip():
            raise WorkflowError("intent must not be empty")
        state = self._mutation_state(expected_revision)
        if state.initialization is None:
            return self._init_required(state)
        if state.pending_discovery_recovery is not None:
            return self._result(
                "PENDING_DISCOVERY_RECOVERY",
                state,
                "bounded discovery recovery requires explicit evidence resolution before intake mutation",
            )
        if assessment is not None:
            if type(assessment) is not IntentAssessment:
                raise WorkflowError("assessment must use the typed Core contract")
            if assessment.operation != AssessmentOperation(operation):
                raise WorkflowError("assessment operation does not match intake operation")
            if any(type(item) is not MaterialDecision for item in assessment.material_ambiguities):
                raise WorkflowError("new material ambiguities require Core-selectable decisions and options")
            if len({item.decision for item in assessment.material_ambiguities if isinstance(item, MaterialDecision)}) != len(assessment.material_ambiguities):
                return self._result("CORE_CONFLICT", state, "material decisions must have distinct identities")
            try:
                validate_assessment_authority(state, assessment)
            except StateValidationError:
                return self._result("CORE_CONFLICT", state, "assessment conflicts with Core capability authority")
            if (
                assessment.confidence == AssessmentConfidence.LOW
                or assessment.capability_state == CapabilityState.UNKNOWN
                or (
                    assessment.material_ambiguities
                    and assessment.selected_capability is None
                )
            ):
                return self._result(
                    "ASSESSMENT_REQUIRED",
                    state,
                    "caller assessment requires clarification before Core mutation",
                )
        policy = IntakePolicy()
        if assessment is not None and assessment.capability_state == CapabilityState.PROJECT_ESTABLISHED:
            route = self._preflight_route(
                state, self._selected_capability(intent, assessment), policy, assessment
            )
            rejected = self._preflight_result(state, operation, route, assessment)
            if rejected is not None:
                return rejected
        pending = state.pending_intake
        if pending is not None:
            primary = state.active_context
            if primary is None:
                raise WorkflowError("pending intake lost its restored primary")
            if assessment is None:
                capability = pending.intent
                route = CapabilityRoute("NO_ESTABLISHED_CAPABILITY")
            else:
                capability = self._selected_capability(pending.intent, assessment)
                route = self._preflight_route(state, capability, policy, assessment)
                rejected = self._preflight_result(state, operation, route, assessment)
                if rejected is not None:
                    return rejected
                if route.code == "CORE_CAPABILITY":
                    return self._result("CORE_CONFLICT", state, "pending work cannot bind an established Core feature")
            token = sha256(str(pending.id).encode("utf-8")).hexdigest()[:12]
            feature = Feature(
                FeatureId(f"feature-{token}"),
                capability,
                LivingSpecId(f"spec-feature-{token}"),
                primary.feature_id,
            )
            classification = self._classification(operation, pending.intent, assessment)
            change = Change(
                ChangeId(f"change-{token}"),
                feature.id,
                pending.intent,
                classification,
                assessment=assessment,
            )
            try:
                saved = self.__core.materialize_pending(state, feature, change)
                saved, needs_clarification = self._persist_safety_facts(
                    saved, policy, classification, route.provenance, assessment
                )
            except ServiceError as error:
                raise WorkflowError("pending intake could not be materialized") from error
            questions = self._questions_for_state(saved) if needs_clarification else ()
            return self._result(
                "NEEDS_CLARIFICATION" if questions else "SIDE_ACTIVE",
                saved,
                "explicit pending intake materialized",
                questions,
            )
        linked = next(
            (
                item
                for item in state.deferred_linked_intents
                if item.intent == intent.strip() and item.capability_target == intent.strip()
            ),
            None,
        )
        if linked is not None and state.active_context is None:
            capability = self._selected_capability(linked.capability_target, assessment)
            route = self._preflight_route(state, capability, policy, assessment)
            rejected = self._preflight_result(state, operation, route, assessment)
            if rejected is not None:
                return rejected
            token = sha256(str(linked.id).encode("utf-8")).hexdigest()[:12]
            feature = (
                next(item for item in state.features if str(item.id) == route.feature_id)
                if route.code == "CORE_CAPABILITY" and route.feature_id is not None
                else Feature(FeatureId(f"feature-{token}"), capability, LivingSpecId(f"spec-feature-{token}"))
            )
            classification = self._classification(operation, linked.intent, assessment)
            change = Change(ChangeId(f"change-{token}"), feature.id, linked.intent, classification, linked.shared_intent_id, assessment)
            try:
                saved = self.__core.materialize_linked(state, str(linked.id), feature, change)
                saved, needs_clarification = self._persist_safety_facts(
                    saved, policy, classification, route.provenance, assessment
                )
            except ServiceError as error:
                raise WorkflowError("linked intent could not be materialized") from error
            questions = self._questions_for_state(saved) if needs_clarification else ()
            return self._result(
                "NEEDS_CLARIFICATION" if questions else "PROPOSED",
                saved,
                "terminal predecessor released linked intent",
                questions,
            )
        if state.active_context is not None:
            active = state.active_context
            if active.side_of is not None:
                if not policy.is_unrelated(intent):
                    blocked = self._intake_guidance_block(state, operation)
                    assert blocked is not None
                    return blocked
                if assessment is not None:
                    capability = self._selected_capability(intent, assessment)
                    route = self._preflight_route(state, capability, policy, assessment)
                    rejected = self._preflight_result(state, operation, route, assessment)
                    if rejected is not None:
                        return rejected
                request_id = IntakeId(f"intake-{sha256(intent.encode('utf-8')).hexdigest()[:12]}")
                try:
                    saved = self.__core.defer_side_request(state, DeferredSideRequest(request_id, intent.strip(), active.side_of, "active side work"))
                except ServiceError as error:
                    raise WorkflowError("side request could not be deferred") from error
                return replace(
                    self._result("DEFERRED", saved, "unrelated request deferred until side completion"),
                    pending_intake_id=str(request_id),
                )
            if not policy.is_unrelated(intent):
                blocked = self._intake_guidance_block(state, operation)
                assert blocked is not None
                return blocked
            if assessment is None:
                return self._start_side(
                    state,
                    intent,
                    intent.strip(),
                    self._classification(operation, intent, None),
                    (),
                    None,
                )
            capability = self._selected_capability(intent, assessment)
            route = self._preflight_route(state, capability, policy, assessment)
            rejected = self._preflight_result(state, operation, route, assessment)
            if rejected is not None:
                return rejected
            if route.code == "CORE_CAPABILITY":
                return self._result("CORE_CONFLICT", state, "side work cannot bind an established Core feature")
            return self._start_side(
                state,
                intent,
                capability,
                self._classification(operation, intent, assessment),
                route.provenance,
                assessment,
            )
        blocked = self._intake_guidance_block(state, operation)
        if blocked is not None:
            return blocked
        capabilities: tuple[str, ...] = (
            () if assessment is not None else policy.linked_capabilities(intent)
        )
        first = (
            assessment.selected_capability
            if assessment is not None and assessment.selected_capability is not None
            else intent.strip() if not capabilities else capabilities[0]
        )
        token = sha256(intent.encode("utf-8")).hexdigest()[:12]
        shared = None if not capabilities else SharedIntent(SharedIntentId(f"shared-{token}"), intent.strip())
        classification = self._classification(operation, intent, assessment)
        units = (first,) if not capabilities else capabilities
        routes: list[CapabilityRoute] = []
        for unit in units:
            route = self._preflight_route(state, unit, policy, assessment)
            if route.code == "DISCOVERY_LIMIT":
                if operation is IntakeOperation.NEW and assessment is None:
                    recovery = self._discovery_recovery(state, intent)
                    if recovery is not None:
                        try:
                            saved = self.__core.record_discovery_recovery(state, recovery)
                        except ServiceError as error:
                            raise WorkflowError("discovery recovery could not be persisted") from error
                        return self._result("DISCOVERY_RECOVERY_REQUIRED", saved, "bounded discovery requires Core-validated evidence recovery")
                recovery = self._recovery_assessment(operation, unit, policy, assessment)
                if recovery is not None:
                    return self._intake(operation, intent, recovery, expected_revision)
                return self._result("DISCOVERY_LIMIT", state, "bounded capability discovery requires a narrower repository")
            routes.append(route)
        route = routes[0]
        parked = next((item for item in state.work_items if item.context is not None), None)
        parked_context = None if parked is None else parked.context
        if (
            parked_context is not None
            and any(
                item.code == "CORE_CAPABILITY"
                and item.feature_id == str(parked_context.feature_id)
                for item in routes
            )
        ):
            return self._result(
                "PARKED_WORK_CONFLICT",
                state,
                "same-feature work remains parked and must resume explicitly",
            )
        if route.code == "ASSESSMENT_CONFLICT":
            return self._result("CORE_CONFLICT", state, "assessment conflicts with Core capability authority")
        if any(item.code == "AMBIGUOUS_CAPABILITY" for item in routes):
            candidates = tuple(
                CapabilityCandidate(feature.id, feature.title)
                for feature in state.features
                if DiscoveryPolicy.matches_capability(feature.title, first)
            )
            if len(candidates) < 2:
                return self._result("AMBIGUOUS_CAPABILITY", state, "multiple established capabilities require explicit selection")
            identifier = ":".join(str(item.feature_id) for item in candidates)
            selection = PendingCapabilitySelection(
                CapabilitySelectionId(f"selection-{sha256(f'{operation}:{first}:{identifier}'.encode()).hexdigest()[:12]}"),
                AssessmentOperation(operation),
                first,
                candidates,
                True,
                tuple(f"feature:{item.feature_id}" for item in candidates),
            )
            try:
                saved = self.__core.record_capability_selection(state, selection)
            except ServiceError as error:
                raise WorkflowError("capability selection could not be persisted") from error
            return self._result("AMBIGUOUS_CAPABILITY", saved, "Core-issued capability selection is required")
        if (
            assessment is None
            and operation == IntakeOperation.CHANGE
            and any(item.code == "CORE_CAPABILITY" for item in routes)
        ):
            return self._result(
                "ASSESSMENT_REQUIRED",
                state,
                "unassessed fallback cannot authorize Core capability mutation",
            )
        if capabilities and any(item.code != "NO_ESTABLISHED_CAPABILITY" for item in routes):
            return self._result("LINKED_TARGET_UNSAFE", state, "linked targets cannot safely reuse established authority")
        for item in routes:
            operation_result = self._operation_route_result(operation, item)
            if operation_result is not None:
                message = (
                    "established capability requires explicit change intake"
                    if operation_result == "ESTABLISHED_CAPABILITY"
                    else "change intake requires an established capability"
                )
                result = self._result(operation_result, state, message)
                return (
                    result
                    if route.feature_id is None
                    else replace(result, feature_id=route.feature_id)
                )
        feature = (
            Feature(FeatureId(route.feature_id), first, LivingSpecId(f"spec-{route.feature_id}"))
            if route.code == "CORE_CAPABILITY" and route.feature_id is not None
            else Feature(FeatureId(f"feature-{token}"), first, LivingSpecId(f"spec-feature-{token}"))
        )
        change = Change(ChangeId(f"change-{token}"), feature.id, intent, classification, None if shared is None else shared.id, assessment)
        try:
            if shared is not None:
                if len(capabilities) < 2:
                    raise WorkflowError("shared intake requires two explicit capabilities")
                linked_target = capabilities[1]
                linked_token = sha256(f"{token}:{linked_target}".encode("utf-8")).hexdigest()[:12]
                state = self.__core.record_shared_intent(
                    state,
                    shared,
                    DeferredLinkedIntent(
                        IntakeId(f"linked-{linked_token}"),
                        shared.id,
                        linked_target,
                        linked_target,
                    ),
                )
            if state.pending_intake is not None:
                state = self.__core.materialize_pending(state, feature, change)
            elif route.code != "CORE_CAPABILITY":
                state = self.__core.record_feature(state, feature)
            state = self.__core.record_change(state, change)
            state, needs_clarification = self._persist_safety_facts(
                state, policy, classification, route.provenance, assessment
            )
        except ServiceError as error:
            raise WorkflowError("intake could not be persisted") from error
        questions = self._questions_for_state(state) if needs_clarification else ()
        return self._result("NEEDS_CLARIFICATION" if questions else "PROPOSED", state, "intent recorded for deterministic research", questions)

    def _start_side(
        self,
        state: State,
        intent: str,
        capability: str,
        classification: Classification,
        provenance: tuple[str, ...],
        assessment: IntentAssessment | None,
    ) -> IntakeResult:
        primary = state.active_context
        if primary is None:
            raise WorkflowError("primary context is unavailable")
        token = sha256(intent.encode("utf-8")).hexdigest()[:12]
        if assessment is not None and assessment.capability_state == CapabilityState.ESTABLISHED:
            raise WorkflowError("side work cannot bind an established Core feature")
        feature = Feature(FeatureId(f"feature-{token}"), capability, LivingSpecId(f"spec-feature-{token}"), primary.feature_id)
        change = Change(ChangeId(f"change-{token}"), feature.id, intent, classification, assessment=assessment)
        try:
            state = self.__core.record_feature(state, feature)
            state = self.__core.activate_side(state, change)
            state, needs_clarification = self._persist_safety_facts(
                state, IntakePolicy(), classification, provenance, assessment
            )
        except ServiceError as error:
            raise WorkflowError("side work could not be activated") from error
        questions = self._questions_for_state(state) if needs_clarification else ()
        return self._result(
            "NEEDS_CLARIFICATION" if questions else "SIDE_ACTIVE",
            state,
            "unrelated side work preserves primary context",
            questions,
        )

    def _selected_capability(
        self, intent: str, assessment: IntentAssessment | None
    ) -> str:
        """Use a caller-selected identity for routing while retaining the original intent."""
        return (
            intent.strip()
            if assessment is None or assessment.selected_capability is None
            else assessment.selected_capability
        )

    def _preflight_route(
        self,
        state: State,
        capability: str,
        policy: IntakePolicy,
        assessment: IntentAssessment | None,
    ) -> CapabilityRoute:
        """Validate supplied assessment authority before any branch materializes Core state."""
        if assessment is not None:
            try:
                validate_assessment_authority(state, assessment)
            except StateValidationError:
                return CapabilityRoute("ASSESSMENT_CONFLICT")
        return self._resolve_capability(state, capability, policy, assessment)

    def _preflight_result(
        self,
        state: State,
        operation: IntakeOperation,
        route: CapabilityRoute,
        assessment: IntentAssessment | None,
    ) -> WorkflowResult | None:
        """Return the branch-safe non-mutating result for an unsafe route."""
        match route.code:
            case "CORE_CAPABILITY" if assessment is None and operation == IntakeOperation.CHANGE:
                return self._result(
                    "ASSESSMENT_REQUIRED",
                    state,
                    "unassessed fallback cannot authorize Core capability mutation",
                )
            case "DISCOVERY_LIMIT":
                return self._result("DISCOVERY_LIMIT", state, "bounded capability discovery requires a narrower repository")
            case "ASSESSMENT_CONFLICT":
                return self._result("CORE_CONFLICT", state, "assessment conflicts with Core capability authority")
            case "AMBIGUOUS_CAPABILITY":
                return self._result("AMBIGUOUS_CAPABILITY", state, "multiple established capabilities require explicit selection")
            case _:
                operation_result = self._operation_route_result(operation, route)
                if operation_result is None:
                    return None
                message = (
                    "established capability requires explicit change intake"
                    if operation_result == "ESTABLISHED_CAPABILITY"
                    else "change intake requires an established capability"
                )
                return self._result(operation_result, state, message)

    def _resolve_capability(
        self,
        state: State,
        intent: str,
        policy: IntakePolicy,
        assessment: IntentAssessment | None,
    ) -> CapabilityRoute:
        """Resolve Core authority before bounded native discovery."""
        if assessment is not None:
            match assessment.capability_state:
                case CapabilityState.ESTABLISHED:
                    feature = next(
                        (item for item in state.features if item.id == assessment.selected_feature_id),
                        None,
                    )
                    if feature is None or feature.title != assessment.selected_capability:
                        return CapabilityRoute("ASSESSMENT_CONFLICT")
                    return CapabilityRoute("CORE_CAPABILITY", str(feature.id), (f"feature:{feature.id}",))
                case CapabilityState.PROJECT_ESTABLISHED:
                    capability = assessment.selected_capability
                    if capability is None:
                        return CapabilityRoute("ASSESSMENT_CONFLICT")
                    if any(item.title == capability for item in state.features):
                        return CapabilityRoute("ASSESSMENT_CONFLICT")
                    return CapabilityRoute(
                        "PROJECT_CAPABILITY",
                        provenance=tuple(
                            f"project-assessment:{item.reference}" for item in assessment.evidence
                        ),
                    )
                case CapabilityState.PLANNED:
                    publication = next(
                        (item for item in state.publications if item.capability == assessment.selected_capability),
                        None,
                    )
                    if publication is not None and publication.feature_id is not None:
                        return CapabilityRoute(
                            "CORE_CAPABILITY",
                            str(publication.feature_id),
                            (f"feature:{publication.feature_id}",),
                        )
                    if any(
                        item.title == assessment.selected_capability for item in state.features
                    ):
                        return CapabilityRoute("ASSESSMENT_CONFLICT")
                    return CapabilityRoute("NO_ESTABLISHED_CAPABILITY")
                case CapabilityState.UNKNOWN:
                    return CapabilityRoute("ASSESSMENT_CONFLICT")
        route = policy.resolve_capability(state, intent, ())
        if route.code != "NO_ESTABLISHED_CAPABILITY":
            return route
        discovery = DiscoveryPolicy(self.__root).capability_candidates(intent)
        if discovery.limited:
            return CapabilityRoute("DISCOVERY_LIMIT")
        return policy.resolve_capability(
            state,
            intent,
            tuple((candidate.name, candidate.provenance) for candidate in discovery.candidates),
        )

    def _discovery_recovery(self, state: State, intent: str) -> PendingDiscoveryRecovery | None:
        discovery = DiscoveryPolicy(self.__root)
        if not discovery.capability_candidates(intent).limited:
            return None
        paths, limited = discovery._paths(_IGNORED_PARTS, entry_limit=MAX_RECOVERY_ENTRIES)
        if limited:
            return None
        terms = tuple(dict.fromkeys(word for word in _WORDS.findall(intent.casefold()) if word not in _NON_CAPABILITY_WORDS))
        phrases = tuple(
            " ".join(terms[start:end])
            for start in range(len(terms) - 1)
            for end in range(start + 2, len(terms) + 1)
        )
        evidence: dict[str, list[DiscoveryRecoveryEvidence]] = {phrase: [] for phrase in phrases}
        eligible = 0
        for path in paths:
            if discovery._is_foreign_speckit_command(path):
                continue
            if not path.is_file() or path.suffix.casefold() not in _TEXT_SUFFIXES:
                continue
            if eligible == MAX_RECOVERY_ELIGIBLE_FILES:
                break
            eligible += 1
            try:
                content = discovery._read_capped(path)
            except OSError:
                continue
            if content is None:
                continue
            relative = discovery._relative(path)
            searchable = (relative.as_posix().casefold(), content.decode("utf-8", errors="ignore").casefold())
            fingerprint = sha256(content).hexdigest()
            for phrase in phrases:
                if any(discovery._contains_complete_phrase(value, phrase) for value in searchable):
                    evidence[phrase].append(
                        DiscoveryRecoveryEvidence(
                            f"evidence-{sha256(f'{phrase}:{relative}:{fingerprint}'.encode('utf-8')).hexdigest()[:12]}",
                            phrase,
                            RepositoryPath(relative.as_posix()),
                            fingerprint,
                        )
                    )
        candidates = tuple(name for name, items in evidence.items() if len(items) >= 2)
        selected = tuple(item for name in candidates for item in evidence[name])
        if not candidates:
            return None
        identifier = sha256(f"{state.revision}:NEW:{intent}:{','.join(candidates)}:{','.join(item.id for item in selected)}".encode("utf-8")).hexdigest()[:12]
        return PendingDiscoveryRecovery(
            f"discovery-{identifier}", AssessmentOperation.NEW, intent.strip(), candidates, selected
        )

    def _recovery_assessment(
        self,
        operation: IntakeOperation,
        intent: str,
        policy: IntakePolicy,
        assessment: IntentAssessment | None,
    ) -> IntentAssessment | None:
        if operation is not IntakeOperation.CHANGE or assessment is not None or policy.linked_capabilities(intent):
            return None
        recovery = DiscoveryPolicy(self.__root).recovery_candidates(intent)
        if recovery.limited or len(recovery.candidates) != 1:
            return None
        candidate = recovery.candidates[0]
        if len(candidate.provenance) < 2:
            return None
        return IntentAssessment(
            AssessmentOperation(operation),
            policy.classify(operation, intent),
            AssessmentConfidence.HIGH,
            CapabilityState.PROJECT_ESTABLISHED,
            candidate.name,
            (candidate.name,),
            None,
            tuple(
                AssessmentEvidence("bounded-recovery", RepositoryPath(reference))
                for reference in candidate.provenance
            ),
            (),
            AssessmentDepth(
                AssessmentDepthValue.NORMAL,
                AssessmentDepthValue.NORMAL,
                AssessmentDepthValue.NORMAL,
            ),
            "bounded recovery found one attributable project-native capability",
        )

    def _classification(
        self,
        operation: IntakeOperation,
        intent: str,
        assessment: IntentAssessment | None,
    ) -> Classification:
        """Use caller classification when present, otherwise preserve legacy defaults only."""
        return IntakePolicy().classify(operation, intent) if assessment is None else assessment.classification

    def _effective_safety_facts(
        self,
        policy: IntakePolicy,
        state: State,
        classification: Classification,
        provenance: tuple[str, ...],
        assessment: IntentAssessment | None,
    ) -> SafetyFacts:
        """Apply deterministic component floors while retaining assessment provenance on Change."""
        facts = policy.research(state, classification, provenance)
        requested = (
            (AssessmentDepthValue.NORMAL,) * 3
            if assessment is None
            else (
                assessment.recommended_depth.review,
                assessment.recommended_depth.verification,
                assessment.recommended_depth.convergence,
            )
        )
        floor = (facts.review_depth, facts.verification_depth, facts.convergence_depth)
        rank = {
            "TARGETED": 0,
            "LIGHT": 1,
            "NORMAL": 2,
            "DEEP": 3,
            "FULL": 4,
        }
        depths = tuple(
            candidate.value if rank[candidate.value] >= rank[minimum or "NORMAL"] else minimum or "NORMAL"
            for candidate, minimum in zip(requested, floor, strict=True)
        )
        assessment_scope = () if assessment is None else tuple(
            f"assessment:{item.reference}" for item in assessment.evidence
        )
        return replace(facts, scope=facts.scope + assessment_scope, review_depth=depths[0], verification_depth=depths[1], convergence_depth=depths[2])

    def _persist_safety_facts(
        self,
        state: State,
        policy: IntakePolicy,
        classification: Classification,
        provenance: tuple[str, ...],
        assessment: IntentAssessment | None,
    ) -> tuple[State, bool]:
        """Persist every effective safety fact and clarify only assessed material ambiguities."""
        state = self.__core.record_safety_facts(
            state,
            False,
            self._effective_safety_facts(
                policy, state, classification, provenance, assessment
            ),
        )
        if assessment is None or not assessment.material_ambiguities:
            return state, False
        active = state.active_context
        if active is None or active.change_id is None:
            raise WorkflowError("research context disappeared after safety facts persisted")
        state = self.__core.record_safety_facts(
            state,
            True,
            replace(
                active.safety_facts,
                material_clarifications=tuple(
                    MaterialClarification(
                        self._question(active.change_id, item.decision).id, item.decision, item.options
                    ) for item in assessment.material_ambiguities
                    if type(item) is MaterialDecision
                ),
            ),
        )
        return (
            self.__core.advance_lifecycle(state, LifecycleState.CLARIFYING, Phase.CLARIFY),
            True,
        )

    def _operation_route_result(
        self, operation: IntakeOperation, route: CapabilityRoute
    ) -> str | None:
        """Return the non-mutating operation guard result for one resolved route."""
        if operation == IntakeOperation.NEW and route.code != "NO_ESTABLISHED_CAPABILITY":
            return "ESTABLISHED_CAPABILITY"
        if operation == IntakeOperation.CHANGE and route.code == "NO_ESTABLISHED_CAPABILITY":
            return "NO_ESTABLISHED_CAPABILITY"
        return None

    def _intake_guidance_block(
        self, state: State, operation: IntakeOperation
    ) -> WorkflowResult | None:
        """Apply canonical intake guidance after compound facade routes are considered."""
        action = Action.NEW if operation is IntakeOperation.NEW else Action.CHANGE
        guidance = for_action(inspect(state), action)
        if guidance.allowed and state.pending_capability_selection is not None:
            return self._result(
                "PENDING_CAPABILITY_SELECTION",
                state,
                "ambiguous capability requires an explicit user selection before intake mutation",
            )
        return (
            None
            if guidance.allowed
            else self._result(
                guidance.reason_code or "LIFECYCLE_UNAVAILABLE", state, guidance.message
            )
        )

    def _mutation_state(self, expected_revision: ExpectedRevision | None) -> State:
        """Bind caller revision authority to the first Core mutation."""
        if expected_revision is None:
            return self.__core.recover()
        return self.__core.mutation_state(expected_revision)

    def _preflight(
        self, action: Action, expected_revision: ExpectedRevision | None
    ) -> tuple[State, WorkflowResult | None]:
        """Bind revision authority, then return pure guidance before any Core mutation."""
        state = self._mutation_state(expected_revision)
        guidance = for_action(inspect(state), action)
        if guidance.allowed:
            return state, None
        return state, self._result(
            guidance.reason_code or "LIFECYCLE_UNAVAILABLE", state, guidance.message
        )

    def _result(self, code: str, state: State, message: str, questions: tuple[ClarificationQuestion, ...] = ()) -> WorkflowResult:
        context = state.active_context
        return WorkflowResult(
            code,
            state.revision,
            None if context is None else str(context.feature_id),
            None if context is None or context.change_id is None else str(context.change_id),
            None if context is None else str(context.phase),
            message,
            questions,
            None if state.pending_intake is None else str(state.pending_intake.id),
            None if context is None else context.focus,
            None if context is None else context.current_work,
            () if context is None else context.pending_work,
            None if context is None else context.blocker,
            None if context is None or context.classification is None else str(context.classification),
            None if context is None or context.review is None else str(context.review.verdict),
            None if context is None else context.verification.sufficient,
            None if context is None else context.safety_facts.review_depth,
            None if context is None else context.safety_facts.verification_depth,
            None if context is None else context.safety_facts.convergence_depth,
        )

    def _init_required(self, state: State) -> WorkflowResult:
        """Return the shared non-mutating result for absent repository authority."""
        return self._result("INIT_REQUIRED", state, "repository authority initialization is required")

    def _questions_for_state(self, state: State) -> tuple[ClarificationQuestion, ...]:
        context = state.active_context
        if context is None or context.change_id is None:
            raise WorkflowError("clarification context is unavailable")
        return self.clarification_questions(context)

    def _questions(
        self, change_id: ChangeId, details: tuple[str, ...]
    ) -> tuple[ClarificationQuestion, ...]:
        return tuple(self._question(change_id, detail) for detail in details)

    def _question(self, change_id: ChangeId, detail: str) -> ClarificationQuestion:
        identifier = sha256(f"{change_id}:{detail}".encode("utf-8")).hexdigest()[:12]
        return ClarificationQuestion(f"question-{identifier}", detail)

    def _review_evidence(
        self, context: WorkingContext, state: State
    ) -> tuple[ReviewEvidence, ...]:
        project_evidence = tuple(item for item in context.safety_facts.scope if item.startswith("project-native:"))
        native = () if not project_evidence else (
            ReviewEvidence(ReviewEvidenceCategory.REQUIREMENTS, ReviewEvidenceKind.REFERENCE, "project-native", True, "bounded native capability evidence", ",".join(project_evidence)),
        )
        return (
            ReviewEvidence(ReviewEvidenceCategory.WORKFLOW, ReviewEvidenceKind.FACT, "Core", True, "active review context"),
            ReviewEvidence(ReviewEvidenceCategory.REQUIREMENTS, ReviewEvidenceKind.REFERENCE, "discovery", True, ",".join(context.safety_facts.scope) or "initialized repository research"),
            ReviewEvidence(ReviewEvidenceCategory.CLARIFICATION, ReviewEvidenceKind.STATUS, "Core", True, "" if context.unresolved_material_decision else "resolved"),
            ReviewEvidence(ReviewEvidenceCategory.CONSTRAINT, ReviewEvidenceKind.STATUS, "Core", False, str(len(context.constraint_ids))),
            ReviewEvidence(ReviewEvidenceCategory.PRODUCT_TRUTH, ReviewEvidenceKind.STATUS, "Core", True, str(len(state.publications))),
            ReviewEvidence(
                ReviewEvidenceCategory.PROGRESS,
                ReviewEvidenceKind.STATUS,
                "Core",
                True,
                "MISSING"
                if context.current_work is None
                or (
                    context.safety_facts.review_depth == "DEEP"
                    and not context.completed_work
                )
                else context.current_work,
            ),
        ) + native
