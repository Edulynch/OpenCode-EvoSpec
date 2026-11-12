# Feature Specification: Core Bootstrap

**Feature**: 001 Core Bootstrap
**Status**: Accepted foundation
**Date**: 2026-09-02

## Overview

Core Bootstrap establishes the smallest persistent product model that future EvoSpec workflows can use to understand a repository, describe its capabilities, track current work, and recover interrupted work. It defines relationships among repository artifacts, Living Specs, changes, a primary active feature, side changes, clarifications, user constraints, and verification observations.

The feature describes observable behavior and product meaning only. It does not prescribe how that behavior is implemented or how state is physically represented.

The feature applies equally to software projects in any domain. Terms such as feature, capability, change, and work refer to product concerns, not to a particular technology or development process.

## Goals

- Establish a discoverable relationship between a repository and its EvoSpec artifacts.
- Preserve one primary active feature and its resumable work context.
- Treat each Living Spec as the current, deliberately maintained product truth for a capability.
- Represent changes through a visible, testable lifecycle.
- Pause and restore primary work when an unrelated side change is undertaken.
- Persist material clarifications and user constraints as decisions that future work can inspect.
- Recover work from persisted state without relying on an LLM conversation.
- Leave room for future internal change classifications and granular semantic lock metadata.
- Keep responsibilities separate from change-scope controls and implementation agents.

## User Scenarios

### US-1: Establish a repository relationship and a primary feature

**Priority**: P0

**Story**: As a project maintainer, I want EvoSpec artifacts to be associated with the repository and one feature to be the primary working context, so future work has a stable starting point.

**Acceptance**

1. **Given** a repository with no EvoSpec working state, **When** Core Bootstrap state is established, **Then** the repository has a recognizable EvoSpec artifact area and a working-state record with no more than one primary active feature.
2. **Given** a primary active feature exists, **When** a future workflow begins without a more specific scope decision, **Then** the primary feature is the default context.
3. **Given** a feature is designated primary, **When** its identity is inspected, **Then** its associated Living Spec and resumable work context can be identified without conversational history.

### US-2: Maintain a Living Spec as product truth

**Priority**: P0

**Story**: As a product owner, I want a capability's expected behavior to remain in a deliberate, evolvable specification, so accepted changes do not depend on stale or forgotten conversations.

**Acceptance**

1. **Given** a capability has an associated Living Spec, **When** its current behavior is reviewed, **Then** the spec describes the currently accepted expected behavior and relevant product decisions.
2. **Given** an accepted change alters a capability, **When** the change reaches its completion decision, **Then** the affected Living Spec is eligible for deliberate update to reflect the new product truth.
3. **Given** an implementation or execution observation differs from a Living Spec, **When** the difference is identified, **Then** it is surfaced as product/runtime drift for later reconciliation and is not silently adopted as product truth.
4. **Given** a rejected or disposable experiment produces an observation, **When** that observation is recorded, **Then** it does not change the Living Spec unless an explicit product decision accepts it.

### US-3: Track a change through its lifecycle

**Priority**: P0

**Story**: As a contributor, I want a change to have a clear lifecycle and recoverable current position, so work can be paused, resumed, accepted, rejected, or abandoned without ambiguity.

**Acceptance**

1. **Given** a change is proposed, **When** it is recorded, **Then** it has an identity, a relationship to its affected feature or capability, a current lifecycle state, and a stated intent.
2. **Given** a change is in progress, **When** work is interrupted, **Then** the persisted state identifies the change, its current phase or focus, its completed work, its current work, its pending work, and its latest verification state.
3. **Given** a change is accepted, **When** its lifecycle is advanced, **Then** it records acceptance and the associated product truth can be updated deliberately.
4. **Given** a change is rejected, **When** its lifecycle is advanced, **Then** it records rejection and any retained rationale remains distinguishable from accepted product truth.
5. **Given** a change is abandoned, **When** its lifecycle is advanced, **Then** it is no longer represented as unfinished active work and its retained context cannot be mistaken for accepted behavior.

### US-4: Pause and restore primary work for a side change

**Priority**: P0

**Story**: As a contributor, I want unrelated work to be isolated temporarily, so completing a side change does not erase the primary feature's context or progress.

**Acceptance**

1. **Given** a primary feature has active focus and unfinished work, **When** an unrelated change is identified, **Then** the unrelated change is recorded as a side change linked to the primary feature and the primary context is preserved before focus moves.
2. **Given** a side change is active, **When** its work is paused or completed, **Then** the preserved primary feature, focus, phase, constraints, clarifications, and progress remain recoverable.
3. **Given** a side change reaches a terminal outcome, **When** restoration is requested or the lifecycle requires return, **Then** the previously preserved primary context becomes active without inventing new progress or losing recorded progress.
4. **Given** multiple side changes are associated with one primary feature, **When** one side change is restored, **Then** each side change remains separately identifiable and the primary feature remains singular.
5. **Given** side work has been completed, **When** the primary feature is restored, **Then** the completed side work remains linked for traceability but is not presented as the primary feature's unfinished work.

### US-5: Persist material clarifications and user constraints

**Priority**: P0

**Story**: As a user, I want consequential decisions and constraints preserved, so future work does not ask me to repeat or silently override them.

**Acceptance**

1. **Given** a clarification answer materially affects behavior, scope, architecture, risk, lifecycle, or product semantics, **When** the answer is accepted, **Then** it is persisted with the affected feature or change and remains distinguishable as an accepted decision.
2. **Given** a user states a constraint that limits or guides product work, **When** the constraint is accepted, **Then** it is persisted with enough context to identify its scope and remains available during related future work.
3. **Given** an ambiguity has no material effect, **When** work is recorded, **Then** no clarification record is required solely for that theoretical ambiguity.
4. **Given** a later decision intentionally changes an earlier clarification or constraint, **When** the change is accepted, **Then** the newer decision supersedes the earlier one without erasing the fact that the earlier decision existed.

### US-6: Recover interrupted work independently of LLM memory

**Priority**: P0

**Story**: As a contributor returning after an interruption, I want enough persisted context to resume safely, even if the previous agent, model, or conversation is unavailable.

**Acceptance**

1. **Given** work stops after a checkpointable state, **When** a different agent or a new conversation inspects the project, **Then** it can identify the active feature, current change, focus, lifecycle phase, accepted decisions, constraints, completed work, current work, pending work, and verification status from persisted state alone.
2. **Given** work stops during a side change, **When** recovery occurs, **Then** the side change and the preserved primary context are both identifiable and neither is confused with the other.
3. **Given** the project is reopened after compaction, exhaustion, an agent change, or a restart, **When** recovery state is inspected, **Then** recovery does not require unrecorded conversational memory.
4. **Given** no work is currently active, **When** recovery state is inspected, **Then** it reports the absence of active work rather than fabricating a focus or progress state.

## Functional Requirements

### Repository artifacts and relationships

- **FR-001 [P0]** The repository MUST have a recognizable EvoSpec artifact area that can contain Living Specs and persisted working state without requiring a particular storage technology.
- **FR-002 [P0]** Each Living Spec MUST be associated with the repository and identify one capability whose expected behavior it describes.
- **FR-003 [P0]** Each change MUST identify the feature or capability it concerns and MUST be traceable to the relevant Living Spec when one exists.
- **FR-004 [P1]** Artifact relationships MUST permit a capability to be found from its Living Spec, its changes, and its current work context without requiring the user to know an internal artifact location.
- **FR-005 [P1]** The artifact relationships MUST support historical traceability for superseded decisions, accepted changes, rejected experiments, and completed side changes without presenting them as current product truth.

### Primary active feature and minimal state

- **FR-006 [P0]** The working state MUST identify zero or one primary active feature, never more than one at a time.
- **FR-007 [P0]** When a primary active feature exists, the working state MUST identify its current focus and lifecycle phase, or explicitly record that no focus is set.
- **FR-008 [P0]** Working state MUST retain the minimum information needed to resume unfinished work: active feature, current change when applicable, focus, phase, accepted clarifications, applicable user constraints, completed work, current work, pending work, and verification state.
- **FR-009 [P0]** The minimum state MUST be inspectable and usable by an agent or process that is different from the one that created it.
- **FR-010 [P1]** The model MUST allow future internal classifications named `TUNE`, `PATCH`, `FEATURE`, `FOUNDATION`, and `EXPERIMENT` to be associated with a change.
- **FR-011 [P1]** Internal classification MUST be determined by the future workflow and MUST NOT require the user to choose a classification as a prerequisite for describing intent.

### Living Specs and truth boundaries

- **FR-012 [P0]** A Living Spec MUST represent accepted expected product behavior, not merely current implementation behavior.
- **FR-013 [P0]** A Living Spec MUST remain open to deliberate evolution after its initial acceptance.
- **FR-014 [P0]** Runtime observations, test observations, implementation accidents, and experiment results MUST remain distinguishable from accepted product truth.
- **FR-015 [P0]** Meaningful product/runtime drift MUST be surfaced for later reconciliation, with no automatic rule that resolves the drift in favor of either source.
- **FR-016 [P1]** An experiment MUST be representable as disposable work whose outcome is either explicitly accepted into product truth or retained as rejected knowledge without changing the Living Spec.

### Change lifecycle

- **FR-017 [P0]** A change MUST support these lifecycle states: `PROPOSED`, `CLARIFYING`, `READY`, `IN_PROGRESS`, `PAUSED`, `VERIFYING`, `ACCEPTED`, `REJECTED`, and `ABANDONED`.
- **FR-018 [P0]** A change MUST have exactly one current lifecycle state at a time.
- **FR-019 [P0]** The lifecycle MUST retain enough history to distinguish accepted, rejected, abandoned, and currently unfinished changes.
- **FR-020 [P0]** A change MUST NOT enter `READY` or `IN_PROGRESS` while a material product decision required for that change remains unresolved.
- **FR-021 [P0]** A change MUST enter `VERIFYING` before it can be marked `ACCEPTED` when the change has produced behavior that requires verification.
- **FR-022 [P0]** Only `ACCEPTED`, `REJECTED`, and `ABANDONED` are terminal lifecycle states for a change; a terminal change MUST NOT remain current unfinished work.
- **FR-023 [P1]** A paused change MUST retain its prior focus, lifecycle state, phase, and resumable context. `PAUSED` MUST be allowed from `IN_PROGRESS`, `VERIFYING` while in `VERIFY`, and `VERIFYING` while in `CONVERGE`. Resuming it MUST restore the exact preserved lifecycle state, phase, focus, and context pair; it MUST NOT invent a new phase, force `IN_PROGRESS`, create a terminal state, waive a gate, or resume automatically.

### Workflow phases, review, verification, and convergence

- **FR-024 [P0]** The workflow MUST represent these persisted phases: `RESEARCH`, `CLARIFY`, `READY`, `IMPLEMENT`, `REVIEW`, `VERIFY`, `CONVERGE`, and `DONE`.
- **FR-025 [P0]** Persisted phases MUST map to lifecycle states as follows: `RESEARCH` under `PROPOSED`; `CLARIFY` under `CLARIFYING`; `READY` under `READY`; `IMPLEMENT` and `REVIEW` under `IN_PROGRESS`; `VERIFY` and `CONVERGE` under `VERIFYING`; and `DONE` under `ACCEPTED`. `PAUSED` retains the exact valid non-terminal lifecycle-state/phase pair from which it was paused. `REJECTED` and `ABANDONED` retain the last valid phase for traceability but are terminal and not resumable.
- **FR-026 [P0]** A change MUST have exactly one lifecycle state and MUST persist its current phase and current action. An unfinished change MUST also persist any supplied durable review verdict and findings, verification evidence and sufficiency, repair count, and resume point.
- **FR-027 [P0]** Core Bootstrap MUST represent the conceptual phase order `RESEARCH` → `CLARIFY` → `READY` → `IMPLEMENT` → `REVIEW` → `VERIFY` → `CONVERGE` → `DONE`; a caller or future workflow decides whether and when to advance it.
- **FR-028 [P0]** Core Bootstrap MUST preserve any review inputs and results supplied by a caller, including intent, work context, product truth, requirements, decisions, constraints, evidence, scope, pending work, and persistent state. It MUST NOT perform review or determine product conformance.
- **FR-029 [P0]** When a caller supplies a review result, Core Bootstrap MUST preserve its atomic verdict as `PASS`, `WARN`, or `BLOCKED` and its relevant findings. `WARN` is nonblocking only when the caller records it as safe to proceed. Core Bootstrap MUST NOT generate a verdict, invoke repair, or advance the phase because of a verdict.
- **FR-030 [P0]** Core Bootstrap MUST persist a caller-supplied repair count and review checkpoints. The count MUST NOT exceed two; when it equals two, the persisted state MUST identify a blocker and safe next step before a caller records the change as `PAUSED`. Core Bootstrap MUST NOT execute repairs or pause a change autonomously.
- **FR-031 [P0]** An explicit read-oriented review inspection MUST NOT advance lifecycle state or phase, perform repair, accept the change, or alter product truth.
- **FR-032 [P0]** Core Bootstrap MUST preserve caller-supplied verification evidence and sufficiency. It MUST NOT gather technical evidence or infer that a technical `PASS` constitutes product conformance.
- **FR-033 [P0]** Core Bootstrap MUST validate that a caller may record `CONVERGE` only when the persisted review verdict is `PASS` or a safe, nonblocking `WARN`, and persisted verification is sufficient. It MUST NOT reconcile Living Specs, decisions, experiments, final state, drift, or primary context autonomously.
- **FR-034 [P0]** When a caller records an implementation alteration after a review or verification checkpoint, Core Bootstrap MUST invalidate the affected persisted checkpoints. It MUST NOT detect implementation changes or initiate review or verification.
- **FR-035 [P1]** Core Bootstrap MUST preserve caller-supplied review, verification, and convergence depth associated with classification and risk. It MUST preserve an explicit experiment acceptance or rejection outcome; rejection MUST NOT change accepted product truth.
- **FR-036 [P0]** Core Bootstrap MUST preserve material ambiguity, significant risk, Constitution conflict, protected-lock modification, or a decision that cannot be safely inferred when a caller records it. It MUST NOT solicit intervention or resolve those conditions.
- **FR-037 [P1]** The conceptual model MUST remain generic and MUST NOT require an enterprise approval process or any new artifact solely for review, verification, convergence, or recovery.

### Primary and side-change relationships

- **FR-038 [P0]** A change identified as unrelated to the primary active feature MUST be representable as a side change linked to that primary feature.
- **FR-039 [P0]** Before side-change focus becomes active, the current primary feature context MUST be preserved as a restorable context.
- **FR-040 [P0]** Preserved primary context MUST include the primary feature identity, current change if any, focus, phase, accepted clarifications, applicable constraints, completed work, current work, pending work, and verification state.
- **FR-041 [P0]** Activating a side change MUST NOT replace, rewrite, or discard the preserved primary context.
- **FR-042 [P0]** Returning from side work MUST restore the preserved primary context, unless the user explicitly accepts a different primary feature decision.
- **FR-043 [P1]** A side change MAY be paused, accepted, rejected, or abandoned independently of its primary feature.

### Clarifications, constraints, and future locks

- **FR-044 [P0]** Accepted material clarifications MUST be persisted and linked to the feature or change whose behavior they determine.
- **FR-045 [P0]** Accepted user constraints MUST be persisted with their scope and relationship to affected product work.
- **FR-046 [P0]** Superseded clarifications and constraints MUST remain distinguishable from current ones and MUST NOT silently override the current decision.
- **FR-047 [P1]** The conceptual model MUST leave a defined relationship point for future granular semantic lock metadata to identify protected aspects, mutable aspects, allowed operations, invariants, and rationale.
- **FR-048 [P1]** Core Bootstrap MUST NOT enforce, evaluate, or resolve semantic locks.

### Responsibility boundaries and independence

- **FR-049 [P0]** The product model MUST remain independent of any particular agent, model, integration, conversation, or interaction surface.
- **FR-050 [P1]** The model MUST keep product intent, product truth, decisions, and continuity separate from any future mechanism that limits implementation scope or measures allowed change.
- **FR-051 [P1]** The model MUST allow a future change-scope control to reference a change without making that control the owner of product intent, Living Specs, or recovery state.

## Lifecycle Transition Rules

The following rules define observable transitions. A future workflow MAY provide different labels for user-facing communication, but it MUST preserve these meanings.

| From | Allowed transition | Condition |
| --- | --- | --- |
| `PROPOSED` | `CLARIFYING` | A material decision is unresolved. |
| `PROPOSED` | `READY` | Intent is sufficiently understood and no required material decision is unresolved. |
| `CLARIFYING` | `READY` | Required material decisions have been accepted and persisted. |
| `CLARIFYING` | `REJECTED` | The proposed change is explicitly declined. |
| `READY` | `IN_PROGRESS` | Work begins with a recoverable current context. |
| `READY` | `ABANDONED` | The proposed change is explicitly abandoned before work begins. |
| `IN_PROGRESS` | `PAUSED` | Work is intentionally suspended or displaced by side work. |
| `IN_PROGRESS` | `VERIFYING` | After `REVIEW`, the current verdict is `PASS` or a safe, nonblocking `WARN`. |
| `IN_PROGRESS` | `ABANDONED` | Work is explicitly stopped without acceptance. |
| `PAUSED` | Preserved non-terminal state | The same change is resumed with its preserved lifecycle state, phase, and context. |
| `PAUSED` | `ABANDONED` | The paused change is explicitly abandoned. |
| `VERIFYING` / `VERIFY` | `PAUSED` | Verification work is intentionally suspended. |
| `VERIFYING` / `CONVERGE` | `PAUSED` | Convergence work is intentionally suspended. |
| `VERIFYING` | `ACCEPTED` | The post-`REVIEW` verdict is `PASS` or a safe, nonblocking `WARN`, verification is sufficient for the change's intent, and `CONVERGE` is complete. |
| `VERIFYING` | `IN_PROGRESS` | Verification reveals work or a decision is still needed. |
| `VERIFYING` | `REJECTED` | Verification or product review rejects the outcome. |

No transition may silently change accepted product truth. Acceptance of a change is the explicit point at which its intended product behavior may be incorporated into the affected Living Spec. Rejection and abandonment preserve traceability but do not change accepted product behavior.

Workflow phase progression is distinct from lifecycle state progression. The valid status/phase combinations are exactly the mappings in FR-025, plus a `PAUSED` change retaining any valid non-terminal pair, including `VERIFYING`/`VERIFY` and `VERIFYING`/`CONVERGE`, and terminal `REJECTED` or `ABANDONED` changes retaining their last valid phase for traceability. No other status/phase combination is valid. A terminal change cannot be resumed; a paused change resumes only to its exact preserved state, phase, focus, and context pair, without terminal, waiver, or automatic behavior.

The conceptual phase flow is:

```text
RESEARCH → CLARIFY → READY → IMPLEMENT → REVIEW → VERIFY → CONVERGE → DONE
```

`RESEARCH` records context and intent. `CLARIFY` records material decisions. `READY` records that required decisions are persisted. `IMPLEMENT` records actual work and its evolving context. `REVIEW` stores a caller-supplied durable verdict. `VERIFY` stores caller-supplied technical evidence. `CONVERGE` represents a caller-confirmed reconciliation of product truth, decisions, experiments, final state, drift, and any primary-context restoration. `DONE` records the accepted outcome. A caller may record repair checkpoints up to the persisted two-repair limit; at exhaustion, the caller records a recoverable blocker, safe next step, and `PAUSED` status.

Core Bootstrap neither executes nor orchestrates the conceptual flow, `/evospec.*` commands, LLM calls, repairs, review, verification, convergence, or Living Spec updates. It represents, validates, and persists state, transitions, and results supplied by a caller. An explicit `/evospec.review` inspection, when implemented by a future workflow, is read-oriented only: it reports the durable review view and MUST NOT advance a state or phase, repair work, accept a change, or alter product truth.

## Persistence and Conceptual Model

Core Bootstrap requires only these conceptual records and relationships:

- **Repository context**: identifies the repository governed by the EvoSpec artifacts.
- **Living Spec**: describes one capability's accepted expected behavior and its deliberate evolution.
- **Feature**: identifies a unit of primary or side work and links that work to relevant product truth.
- **Change**: records intent, lifecycle state, affected feature or capability, classification when available, decisions, progress, and outcome.
- **Working state**: identifies the primary feature, current focus, current phase, current action, current change when applicable, and verification state.
- **Restorable context**: preserves the primary working state while a side change is active.
- **Decision record**: preserves an accepted material clarification or user constraint, including its scope and whether it remains current.
- **Observation**: records runtime, test, implementation, or experiment information that may require reconciliation and is not product truth by default.

For unfinished work, the working state or its related change record MUST durably identify exactly one lifecycle state, the current phase and action, the latest review verdict and findings, verification evidence and its sufficiency, the repair count, and the resume point. These are recovery facts, not requirements for a particular physical record or storage format.

These records MUST be related by stable identities and references. The model MUST avoid duplicate sources of truth for the active feature and current work. It MUST preserve enough history to explain how current product truth and current work were reached, without requiring a separate artifact for every interaction.

## Invariants

1. There is never more than one primary active feature.
2. There is never more than one current lifecycle state for a change.
3. A primary feature's context remains recoverable while a linked side change is active.
4. A terminal change is not current unfinished work.
5. Accepted material decisions are available to related future work.
6. Superseded decisions remain traceable but do not override current decisions.
7. Runtime observations and experiments do not become product truth without explicit acceptance.
8. Product truth and runtime truth can be compared and reported as drift without an automatic resolution.
9. Recovery is possible from persisted state without the prior LLM conversation.
10. The conceptual model contains no dependency on a specific agent, model, vendor, framework, storage technology, or interaction surface.
11. The model supports future granular lock metadata without enforcing locks in this feature.
12. The model preserves the separation between product responsibility, change-scope responsibility, and implementation responsibility.
13. A change has exactly one lifecycle state, with a recoverable phase and action within that state.
14. Review and verification checkpoints are invalid after implementation alteration.
15. Convergence cannot complete without an acceptable review verdict and sufficient verification evidence.
16. A caller-supplied repair count never exceeds two persisted iterations; at exhaustion, persisted state contains a blocker and safe next step before the caller records `PAUSED`.
17. Core Bootstrap does not execute or orchestrate workflow actions; it only represents, validates, and persists caller-supplied state and results.

## Acceptance Criteria

Core Bootstrap is accepted when all of the following are true:

- A repository can be related to a recognizable EvoSpec artifact area containing Living Specs and working state.
- A single primary active feature can be identified, or the absence of one can be represented explicitly.
- A feature, its Living Spec, its changes, and its current work can be traced through stable relationships.
- An unfinished change can be inspected and resumed using persisted context alone.
- An unrelated side change can be activated without losing the primary feature's context, and that context can be restored afterward.
- Material clarifications and user constraints persist, remain scoped, and support later supersession without erasing history.
- Change lifecycle transitions obey the stated allowed transitions and terminal-state rules.
- The conceptual workflow order is `RESEARCH` → `CLARIFY` → `READY` → `IMPLEMENT` → `REVIEW` → `VERIFY` → `CONVERGE` → `DONE`, with each status/phase combination limited to the stated mappings; `PAUSED` preserves a valid non-terminal pair and terminal outcomes preserve their last valid phase without resumption.
- Unfinished work persists its current phase and action, review verdict and findings, verification evidence and sufficiency, repair count, and resume point alongside exactly one lifecycle state.
- Holistic review considers product intent, work context, governing material, actual changes, evidence, truth boundaries, scope, pending work, and persistent state, and distinguishes `PASS`, `WARN`, and `BLOCKED`.
- A caller cannot persist more than two repair iterations; at exhaustion, it records a paused change with a blocker and safe next step.
- Caller-supplied verification evidence is persisted separately from caller-supplied product-conformance review results.
- `CONVERGE` is valid only after an acceptable persisted review verdict and sufficient persisted verification; reconciliation and restoration are caller responsibilities.
- Core Bootstrap represents, validates, and persists caller-supplied state and results; it does not execute `/evospec.*`, LLM calls, repair, review, verification, convergence, or Living Spec updates.
- Review, verification, and convergence depth adapts to `TUNE`, `FEATURE`, `FOUNDATION`, and `EXPERIMENT`, including explicit experiment acceptance or rejection.
- Accepted changes can update Living Specs deliberately, while rejected experiments and runtime observations remain outside product truth unless explicitly accepted.
- Meaningful product/runtime drift is surfaced for later reconciliation.
- Future internal classification names and future granular lock metadata have clear relationship points without requiring user classification or lock enforcement now.
- The model remains agent independent and does not absorb the responsibility of future change-scope controls.
- No requirement depends on a domain-specific product concept or implementation technology.

## Success Criteria

- **SC-001**: A new contributor can identify the current primary feature, current work, and recovery context without access to the original conversation.
- **SC-002**: A simulated interruption and restoration exercise recovers all required active and side-change context with no lost or invented progress.
- **SC-003**: A simulated material clarification is available to a later related change, while a non-material ambiguity creates no unnecessary decision record.
- **SC-004**: A simulated accepted change updates the affected Living Spec only through an explicit product decision, and a rejected experiment leaves product truth unchanged.
- **SC-005**: A simulated difference between expected and observed behavior is surfaced as drift and remains unresolved until an explicit reconciliation decision.
- **SC-006**: The same conceptual records can describe projects in multiple software domains without adding domain-specific rules.
- **SC-007**: A future workflow can classify changes internally and associate granular lock metadata without asking the user to select a classification or enforcing locks in Core Bootstrap.

## Non-Goals

Core Bootstrap does not define or implement:

- Implementation of commands, including command syntax and interfaces. This spec conceptually defines the read-oriented `/evospec.review` behavior described above.
- Any programming language, framework, vendor, storage technology, database, file encoding, or physical storage format.
- An implementation plan, task breakdown, code structure, class definition, schema definition, or test harness design.
- Project autodiscovery, repository initialization, or automatic creation of future workflows.
- Automatic change classification or a user-facing classification choice.
- Semantic lock enforcement, lock conflict resolution, or lock authorization.
- ChangeBudget integration, change-budget enforcement, or measurement of implementation scope.
- Version-control operations or ownership of actual repository history.
- Implementation-agent behavior, generated code, execution procedures, or build and deployment behavior.
- A particular domain model, product vocabulary, lifecycle beyond the generic change lifecycle defined here, or assumptions about mobile, web, backend, game, library, or tooling projects.
- Automatic reconciliation of product/runtime drift.
- Execution or orchestration of `/evospec.*`, LLM calls, repair, review, verification, convergence, or Living Spec updates; Core Bootstrap only represents, validates, and persists caller-supplied state and results.
- A requirement that every change produce a separate artifact, ceremony, explanation, or permanent record beyond what is needed to preserve material product truth and recoverable work.
