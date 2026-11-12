# Implementation Plan: EvoSpec Feature 002 MVP Workflow

## 1. Scope and outcome

Implement one usable, persisted vertical workflow over the accepted Core Bootstrap. A caller supplies natural language intent and repository access. The workflow discovers deterministic evidence, classifies the work internally, asks only material clarification questions, records recoverable work, accepts implementation observations, performs review and verification, and converges accepted product truth through Core Bootstrap.

The workflow defines exactly seven command concepts: `init`, `new`, `change`, `clarify`, `continue`, `status`, and `review`. They are adapters to one canonical `WorkflowService`, not separate state machines. The service API is the primary surface and is independent of agents, models, conversations, and transports. A thin local `argparse` CLI is optional and, if provided, may expose only these seven commands as an adapter over that API; direct API dogfooding remains supported.

This feature extends Core Bootstrap only where the concrete Feature 002 workflow cannot satisfy its requirements with the current implementation. Core remains the sole authority for state, immutable publication, `WorkingContext`, lifecycle validity, atomic persistence, terminal semantics, recovery, and exact side restoration. No second workflow-state store is introduced.

## 2. Governing inputs and decisions

The governing specification is [spec.md](spec.md), including all nine clarified decisions:

This reconciliation preserves all 37 functional requirements, 11 success criteria, nine clarifications, existing exclusions, Core authority, and the persisted dogfooding commitment.

1. Only one side change may be active. A new unrelated request is persisted as deferred work until the active side change is terminal and the primary context is restored.
2. Unavailable verification is recorded as unavailable or insufficient. It blocks convergence and leaves work unfinished until it is paused or explicitly rejected.
3. An accepted Constitution remains authoritative. Conflicting candidates remain attributed evidence and block only materially affected decisions.
4. Shared intent is preserved for multi-capability requests. Linked per-capability changes are created only when lifecycle, review, verification, or acceptance can differ.
5. One persisted dogfooding scenario must cover initialization, new work, an established behavior change, material clarification, side work, interruption and recovery, status, review, verification, convergence, and done without conversation memory.
6. The local CLI is optional and, when present, is a thin `argparse` adapter exposing only the seven command concepts. OpenCode slash registration, chat or natural-language transport/interception, SDD-First plugin behavior, and Claude or Codex integrations are excluded.
7. A Core-owned `SharedIntent` may relate `DeferredLinkedIntent` entries for unfinished independent capability units. These entries carry shared/link identity, capability target, and minimal intake metadata only. They have no status, phase, `WorkingContext`, or lifecycle-bearing `Change`; a normal `Change` is materialized only through Core's existing creation path after the current linked unit reaches a terminal outcome. `PAUSED` and `BLOCKED` linked units remain current owners and do not permit materialization. Every materialized `Change` has exactly one Core-owned lifecycle owner and `WorkingContext`, with no second state authority, ownerless change, duplicate context, nested side stack, task queue, or scheduler.
8. `continue_work()` is state-specific: non-paused active work is read/reconstructed only; paused work returns through a validated Core-backed path to its preserved pair; blocked work reports its block and required action without clearing it; terminal work is reported without reopening. `status()` is always read-only, and neither operation implements, resolves strategy or clarification, clears blocks, or bypasses gates.
9. Review uses compact typed evidence and finding records owned by Core, with deterministic `BLOCKED > WARN > PASS` evaluation. `WorkflowService.clarify(...)` retrieves unresolved stable questions, persists partial answers through Core, and returns structured remaining-question results; required material questions prevent `READY`, while zero unresolved questions complete clarification.

The design also follows `.specify/memory/constitution.md`, especially SDD First Convergence, Living Product Truth, Research Before Established Change, Active Feature Continuity, Persistent State Outside the LLM, Material Only Clarification, Proportional Process, Product Truth and Runtime Truth, Disposable Experiments, Tool Responsibility Separation, Agent Independence, Evidence Calibrated Autodiscovery, and Deterministic Testability and Recovery.

Feature 001 remains the accepted foundation. Its `State` is the mutable authority, `WorkingContext` is the canonical active and preserved context, lifecycle and phase mappings are authoritative, and all writes use the existing atomic publication boundary. The current implementation is in `evospec/domain.py`, `evospec/lifecycle.py`, `evospec/persistence.py`, `evospec/ports.py`, and `evospec/services.py`, with `CoreService` as the caller-controlled mutation boundary.

### Language and dependency decision

Use CPython 3.11 or newer and the standard library only. Use `pathlib` for repository paths and traversal, `json` for persisted data, `hashlib` for deterministic content fingerprints, `re` only for bounded structural matching, `dataclasses` and `enum` for typed records, `typing.Protocol` for ports, `argparse` for the CLI, and `unittest` with `tempfile` for tests. `subprocess` is permitted only for caller-approved verification argv; discovery and research never execute project commands. Do not add packages, an LLM runtime, network access, a server, or a background worker.

The Core Bootstrap plan's serialization and atomic write rules remain in force: stable JSON encoding, strict decoding, repository-relative POSIX references, same-directory temporary files, `os.replace`, and recovery from the authoritative state reference. The workflow must call Core APIs rather than reproduce these rules.

### Authority and responsibility boundary

`WorkflowService` owns orchestration policy and user-facing result composition. It may discover files, classify intent, select evidence depth, identify material ambiguity, produce questions, inspect supplied implementation observations, evaluate review and verification inputs, and decide whether explicit convergence is justified.

Core Bootstrap owns:

- authoritative persisted `State` and revision checks;
- lifecycle and status/phase validation;
- the canonical active and preserved `WorkingContext`;
- atomic persistence and safe recovery;
- immutable Living Spec publication and authoritative publication references;
- decision, observation, drift, checkpoint, review, verification, repair, deferred-request, and linked-change records once supplied by the workflow;
- terminal archiving and exact primary side-context restoration.

Core does not discover repositories, classify intent, ask questions, invoke agents, execute implementation, run research, review behavior, gather verification evidence, reconcile product truth, enforce locks or ChangeBudget, or advance work autonomously. The workflow does not write `.evospec/state.json` directly and does not maintain a parallel lifecycle or recovery record.

## 3. Architecture

The call path is:

```text
optional argparse CLI or another caller
            |
            v
      WorkflowService
       /    |     \
 discovery policies  assurance policies
            |
            v
        CoreService
            |
            v
 authoritative .evospec state and immutable records
```

The service receives a repository root, a user intent or structured answer, and optional caller-supplied implementation or execution observations. It reads the current state through Core, constructs domain values, applies deterministic policies, and submits one validated Core mutation at each persistence boundary. Returned results contain stable identifiers, current state, atomic messages, questions, evidence summaries, and explicit blocked or deferred reasons.

Shared policies are used by every command:

- `DiscoveryPolicy` finds and labels repository evidence.
- `ResearchPolicy` chooses ordered sources and proportional depth.
- `IntakePolicy` normalizes intent and classifies it as `TUNE`, `PATCH`, `FEATURE`, `FOUNDATION`, or `EXPERIMENT` without requiring user selection.
- `ClarificationPolicy` detects material ambiguity and creates atomic questions.
- `AssurancePolicy` builds review inputs, review verdicts, verification requirements, and convergence readiness.
- `ConvergencePolicy` performs explicit product/runtime reconciliation, experiment acceptance or rejection, drift handling, and final publication decisions.

Policies are pure or depend on narrow read-only ports. They do not mutate Core state. `WorkflowService` is the only workflow facade that coordinates them and `CoreService`.

## 4. Additive Core extensions required by Feature 002

The following are acceptance blockers identified by comparing Feature 002 with the implemented Core. They are additive changes to existing domain, lifecycle, persistence, ports, and service authority. They must not become a second state model.

### 4.1 Valid empty repository state with attributed discovery evidence

Current recovery returns `State(0, None)` when `state.json` is absent, but there is no durable repository context or discovery evidence. Add a repository initialization record to the authoritative Core state, with a stable repository identity, initialization status, accepted Constitution reference when present, and attributed discovery evidence. Evidence must include repository-relative path, evidence kind, strength such as `EXPLICIT`, `STRONG`, or `PROVISIONAL`, deterministic content fingerprint, and source relationship. An empty initialized state must explicitly contain no active feature and no fabricated focus or progress.

Add Core operations to establish or inspect valid empty state idempotently. Re-running initialization must preserve accepted state and product truth, add no duplicate primary feature, and report malformed or incomplete state without overwriting it. Discovery evidence is not constitutional authority. Only an explicitly accepted Constitution is authoritative, while conflicting candidates remain evidence and carry their conflict relationship. The workflow decides whether a conflict affects a decision.

### 4.2 One deferred side request

Current Core supports one active side context but has no durable pending intake. Add an optional singular side-specific `DeferredSideRequest` to authoritative state, containing stable request identity, original intent, relation to the primary feature, reason for deferral, and known target hints. Core validates that it exists only while a side context is active and rejects a second request. One Core terminal-side completion mutation atomically archives the side result, restores the exact preserved primary context, removes `DeferredSideRequest`, and creates a singular persisted `PendingIntake` when a deferred request exists. `PendingIntake` is Core-owned, non-lifecycle-bearing, and not a `Change`, `WorkingContext`, or `Active Feature`; it cannot replace `active_context` or `preserved_primary_context`, execute automatically, or act as a queue or scheduler. A later explicit `new` or `change` Core intake mutation validates `PendingIntake`, creates or activates normal Core-owned work, and consumes `PendingIntake` only on successful commit; before that commit, the prior authoritative state, including `PendingIntake`, remains intact.

### 4.3 Shared intent and linked multi-capability changes

Current `Change` points to one feature and `WorkingContext` tracks one current change. Preserve that invariant. Add a minimal Core-owned `SharedIntent` relationship with `DeferredLinkedIntent` entries or references for unfinished independent capability units. Each deferred linked intent carries shared/link identity, capability target, and minimal intake metadata, but no status, phase, `WorkingContext`, or lifecycle-bearing `Change`. Materialize a normal `Change` only through Core's existing creation path after the current linked unit reaches a terminal outcome. `PAUSED` and `BLOCKED` linked units remain current owners and do not permit next-intent activation. Each materialized change has exactly one Core-owned lifecycle owner and `WorkingContext`, one feature, one lifecycle, and its own review, verification, and acceptance outcome. The workflow creates links only when lifecycle, review, verification, or acceptance differs, and may keep one change when those dimensions are identical. Shared intent remains traceable regardless of how many linked changes are created. `DeferredSideRequest` and `PendingIntake` remain distinct from `DeferredLinkedIntent`. Do not create a second state authority, ownerless changes, duplicate contexts, a nested side stack, a task queue, or a scheduler.

### 4.4 Pause from VERIFY and CONVERGE for unavailable verification

Extend the existing Core lifecycle transition table and validation to allow `VERIFYING/VERIFY -> PAUSED/VERIFY` and `VERIFYING/CONVERGE -> PAUSED/CONVERGE`, in addition to existing valid pause transitions. Preserve the exact paused lifecycle-state, phase, focus, context, evidence, blocker, and safe next step. `PAUSED` resumes only to that exact preserved pair and never waives a gate or resumes automatically. Insufficient or unavailable verification never permits convergence and never becomes a successful verification result.

### 4.5 Explicit drift reconciliation

Current Core can record unresolved `Drift`, but the workflow needs an explicit, durable reconciliation decision that does not silently select product or runtime truth. Add a Core-owned reconciliation record or extension to the drift record with decision identity, outcome such as `KEEP_PRODUCT_TRUTH`, `ADOPT_OBSERVED_BEHAVIOR`, or `REJECT_CHANGE`, rationale, and resolved status. The mutation must require an explicit caller decision and preserve the prior unresolved drift history. Adoption of observed behavior is eligible to publish a new immutable Living Spec only through the normal convergence publication boundary. Reconciliation is never inferred from a passing test, implementation state, or text comparison.

All extensions use the existing deterministic codec, revision checks, atomic write primitive, strict recovery validation, immutable history pattern, and public Core service boundary. Update schema version deliberately with strict decoding and no silent migration of unknown data.

### 4.6 Typed review evidence and findings

Add compact Core-owned `ReviewEvidence` and `ReviewFinding` records to the existing review state. `ReviewEvidence` strictly contains a typed category and kind, source attribution, a required or optional indicator, concise value or status, and an optional capability or work reference. `ReviewFinding` strictly contains typed severity and blocking category, a concise message and rationale, a source or evidence reference, and a resolution state only where the current review spec requires it. Both records must serialize, validate, recover, and round-trip losslessly through the existing state codec. Only `CoreService` may validate or mutate persisted review records, before `WorkflowService` performs REVIEW evaluation; there is no parallel review persistence.

## 5. Public agent-independent API

Expose a small typed API from a new workflow module and package surface. Names below are normative enough for implementation, while exact dataclass field ordering may follow existing style.

```text
WorkflowService.open(root: Path) -> WorkflowService
WorkflowService.init() -> InitResult
WorkflowService.new(intent: str) -> IntakeResult
WorkflowService.change(intent: str) -> IntakeResult
WorkflowService.clarify(answers: tuple[ClarificationAnswer, ...] = ()) -> ClarificationResult
WorkflowService.continue_work() -> ContinueResult
WorkflowService.status() -> StatusResult
WorkflowService.review() -> ReviewInspectionResult
WorkflowService.record_progress(...) -> WorkflowResult
WorkflowService.record_review(...) -> WorkflowResult
WorkflowService.record_verification(...) -> WorkflowResult
WorkflowService.converge(...) -> WorkflowResult
WorkflowService.reject(...) -> WorkflowResult
WorkflowService.pause(...) -> WorkflowResult
```

`new` and `change` accept non-empty natural language intent and never accept classification as a required input. `clarify` accepts stable question IDs and atomic answers. Implementation and execution evidence are caller supplied through typed records or narrow ports, not obtained from an agent runtime. Every result includes the authoritative revision, active and preserved context summaries, stable change IDs, phase, lifecycle state, questions, evidence references, and a status code such as `READY`, `NEEDS_CLARIFICATION`, `BLOCKED`, `DEFERRED`, `PAUSED`, or `DONE`.

Read-only inspection methods must never persist or advance state. Mutation methods must load current Core state and submit caller facts through Core, so stale revisions and invalid lifecycle pairs are rejected. No API exposes raw persistence internals as a second authority. `continue_work()` is exactly state-specific: for non-paused active work it only reads or reconstructs all context fields and reports the next safe action; for `PAUSED` it performs only a validated Core-backed return to the exact preserved lifecycle-state, phase, focus, and context pair; for `BLOCKED` it reports the block and required action without clearing it; and for terminal state it reports completion without reopening. It reports valid empty state without inventing work. `status()` is always read-only. Neither method implements work, chooses strategy, resolves clarification, clears a block, or bypasses a gate.

## 6. Thin command-line adapter

If provided, add one optional thin local `argparse` entry module with exactly seven subcommands: `init`, `new`, `change`, `clarify`, `continue`, `status`, and `review`. `new` and `change` accept one intent argument. `clarify` accepts question IDs and answers in a deterministic representation. `init`, `continue`, `status`, and `review` accept only repository selection and read or workflow options needed by the facade. The adapter opens `WorkflowService`, calls one canonical agent-independent facade method, renders concise atomic output, maps typed workflow errors to stable nonzero exit codes, and never discovers files, parses state, performs policy, or calls Core directly. Direct API use remains a supported dogfooding path.

Do not add command concepts for locks, verify, converge, or experiments in this feature. Verification and convergence are public service operations for a future caller or narrow adapter, but the optional CLI surface remains exactly the seven specified concepts. Do not add OpenCode slash registration, chat or natural-language transport/interception, SDD-First plugin behavior, Claude or Codex integrations, or coupling to a chat protocol, agent identity, model identity, or OpenCode.

## 7. Deterministic discovery and research

Discovery is filesystem and content evidence, not semantic certainty. Given the same repository tree and file bytes, it produces the same ordered evidence. Traverse only repository-relative paths in a deterministic POSIX lexical order, skip `.git`, `.evospec` internals, caches, generated bytecode, and files exceeding a documented bounded size. Read UTF-8 text when decodable and record binary or unreadable files as observed metadata without pretending to understand their meaning.

Initial discovery considers existing EvoSpec artifacts, `AGENTS.md`, `README` files, `.specify` materials, Constitution candidates, project metadata, source and test roots, and likely Living Spec locations. It records path, kind, content hash, evidence strength, and reason. Explicit accepted Constitution material is authoritative. A matching phrase or filename is only a candidate or supporting evidence. Conflicting candidates remain attributed and affect only decisions that require those rules.

For `change`, research order is fixed: active feature and current change, authoritative Living Specs, accepted decisions and constraints, rejected experiments and observations, related feature and change links, repository instructions and Constitution evidence, then code and tests when the prior evidence indicates they are relevant. `new` uses the same sources to detect an existing capability and recommends or routes to `change` when overlap is established by explicit relationships or deterministic evidence. Text matching may identify candidates, but only user decisions, persisted relationships, or authoritative references establish semantic overlap or conformance.

Depth is selected by classification, risk, uncertainty, and affected relationships. `TUNE` uses focused evidence, `PATCH` uses affected behavior and constraints, `FEATURE` uses normal related context, `FOUNDATION` expands cross-cutting impact, and `EXPERIMENT` records the smallest evidence set needed to learn while preserving rejection or promotion. The selected depth and its rationale are persisted as safety facts or evidence metadata through Core.

## 8. Intake, clarification, and evidence handling

Intake creates a stable Core-owned `SharedIntent`, selects the default primary context, classifies internally, identifies affected capabilities, and records a proposed change through Core. Independent unfinished capability units remain `DeferredLinkedIntent` entries or references until the current linked unit reaches a terminal outcome, then Core materializes each normal `Change` through its existing creation path. `PAUSED` and `BLOCKED` linked units remain current owners and do not permit materialization. Deferred linked entries carry only shared/link identity, capability target, and minimal intake metadata. If no primary feature exists, it creates one only after initialization has established valid empty repository state. Related requests use the primary feature by default. Unrelated requests become side changes only after Core has preserved the complete primary context; `DeferredSideRequest`, `PendingIntake`, and `DeferredLinkedIntent` remain distinct concepts.

If side work is already active, a new unrelated request is stored as the singular `DeferredSideRequest`. It is not activated, does not receive an active lifecycle, and cannot overwrite the preserved primary context. After side work reaches `ACCEPTED`, `REJECTED`, or `ABANDONED`, one Core terminal-side completion mutation atomically finalizes the side outcome, restores the primary context exactly, removes `DeferredSideRequest`, and creates persisted `PendingIntake`. Only a later explicit `new` or `change` may atomically materialize `PendingIntake` into normal Core-owned work and consume it after successful creation; it is never executed silently.

Clarification produces one atomic question per material decision. A question is material only when alternatives can change behavior, architecture, scope, risk, lifecycle, or product semantics. Each question includes stable ID, affected feature or change, concise prompt, alternatives when known, impact, and blocking status. Question generation remains in `ClarificationPolicy`. Explicit `WorkflowService.clarify(...)` retrieves unresolved stable questions, accepts and persists any partial answers through Core, detects remaining required questions, and returns a structured result. Zero unresolved required questions completes clarification. Accepted answers become scoped Core decisions. Unresolved material decisions keep work in `CLARIFYING` and block `READY` and `IN_PROGRESS`. Non-material uncertainty remains evidence or an assumption and does not create ceremony. Superseding an answer uses Core's immutable decision history and current-reference behavior.

Evidence records source path or supplied observation, deterministic fingerprint where applicable, evidence kind, strength, scope, and interpretation boundary. Runtime, test, implementation, and experiment observations remain separate from accepted product truth. Rejected experiments remain knowledge. Accepted experiments require explicit product acceptance before publication. Verification evidence has a separate sufficiency field and never doubles as a review verdict.

## 9. Workflow behavior by lifecycle phase

### `RESEARCH` under `PROPOSED`

Load authoritative state, establish or inspect repository context, run deterministic discovery and ordered research, create or identify features and changes, classify internally, and persist evidence and safety facts. Detect related versus unrelated work. Do not change accepted product truth.

### `CLARIFY` under `CLARIFYING`

Persist material questions and accepted answers. Re-run only the affected classification or research policy after an answer. Remain blocked while any required decision or affected Constitution conflict is unresolved. `clarify` uses this same boundary and never creates a parallel workflow.

### `READY` under `READY`

Enter only when intent, scope, required decisions, constraints, and relevant evidence are persisted. Record a recoverable resume point. For a linked multi-capability request, only the current linked unit may be materialized as a `READY` and lifecycle-bearing `Change`. All remaining units stay `DeferredLinkedIntent` entries with no lifecycle, phase, status, or `WorkingContext` until their predecessor linked `Change` is terminal; `PAUSED` and `BLOCKED` predecessors do not permit activation.

### `IMPLEMENT` under `IN_PROGRESS`

The caller performs implementation outside EvoSpec and reports current action, focus, completed work, current work, pending work, and resume point through Core. Every meaningful alteration after review or verification invalidates affected checkpoints and returns the active context to implementation through Core's alteration operation.

### `REVIEW` under `IN_PROGRESS`

Automatic review consumes Core-validated `ReviewEvidence` records covering requirements and objectives, original intent, accepted clarifications, user constraints, Constitution facts, Living Spec references, completed work, current work, pending work, product truth and runtime drift, caller or integration observations as non-authoritative evidence, technical evidence metadata, actual changes, `WorkingContext`, scope, and persisted state. It records Core-validated `ReviewFinding` entries and one `ReviewVerdict` through the existing review state, with deterministic precedence `BLOCKED > WARN > PASS`. Missing required evidence is `BLOCKED` or explicit insufficient review, never `PASS`. Auto review evaluates available evidence while in `REVIEW`, uses no embedded LLM, and never launches an AI reviewer. Explicit review uses the same structured model read-only. The explicit `review` command only reads and renders the durable review view. It never repairs, advances, accepts, or changes truth. `VERIFY` remains separate and may occur after review `PASS`.

### `VERIFY` under `VERIFYING`

Select verification depth from classification, risk, and uncertainty. The caller may supply evidence directly or approve an explicit argv verification command; the narrow executor uses `subprocess` without a shell and records the command, outcome, and availability. Discovery and research never execute discovered commands, and an unavailable command is not retried or replaced automatically. Record evidence and sufficiency separately from review. If tooling or environment is unavailable, record `unavailable` or `insufficient`, preserve the blocker, and do not permit convergence. The caller may pause from `VERIFY` with its exact lifecycle-state, phase, focus, and context pair and safe next step, or explicitly reject.

### `CONVERGE` under `VERIFYING`

Core permits entry only after acceptable review and sufficient verification. The workflow explicitly compares intended product truth with runtime truth, resolves or records drift, handles experiment outcome, confirms final state and restoration, and obtains any required product decision. A warning is blocking unless explicitly marked safe to proceed. Unresolved drift blocks acceptance. A caller may pause from `CONVERGE` with its exact lifecycle-state, phase, focus, and context pair; resume restores that pair without automatic continuation or waiver. A successful product-truth change is published immutably before Core publishes the state reference. Failure leaves the previous authority intact.

### `DONE` under `ACCEPTED`

Core archives the exact terminal context, clears unfinished active work, and retains all linked history. Rejected and abandoned changes are terminal archives and cannot resume. A terminal side change uses one Core completion mutation to atomically finalize the side outcome, restore the preserved primary context exactly, remove `DeferredSideRequest`, and create persisted `PendingIntake` when present. `PendingIntake` remains available across reload until a later explicit atomic `new` or `change` materialization succeeds and consumes it.

## 10. Review, verification, convergence, experiments, and recovery

Review repair is capped at two persisted iterations. At two, the workflow records a blocker and safe next step, then pauses without starting a third repair. Core validates the cap but never initiates repair.

Convergence has a guard table: acceptable review means `PASS` or safe `WARN`; verification must be sufficient; all required material decisions must be resolved; unresolved Constitution conflicts affecting the change must be resolved; required drift must have an explicit reconciliation; and experiment outcome must be explicit. Any failed guard returns a structured blocked result and leaves product truth unchanged.

`continue` recovers only from Core's authoritative state. For non-paused active work it reads or reconstructs and reports all context fields and the next safe action without mutating state. It reports no active work for an empty valid state, returns a paused change only through a validated Core-backed transition to its exact preserved lifecycle-state, phase, focus, and context pair, reports a blocked change with its block and required action without clearing it, observes exact primary restoration and persisted `PendingIntake` after side terminal completion, and reports terminal work without reopening. It may report `PendingIntake` presence but never materializes or executes it. It reports recovery unavailable for malformed, stale, missing, or inconsistent authority. It never reconstructs progress from filenames, timestamps, directory order, Markdown text, or conversation memory, and never implements, resolves strategy or clarification, clears blocks, or bypasses gates.

`status` is a read-only atomic view containing repository initialization state, primary feature, active and preserved contexts, `DeferredSideRequest` during active side work, persisted `PendingIntake`, current change or linked changes, focus, action, phase, lifecycle, decisions, constraints, progress, review, verification, blockers, drift, and resume point. It explicitly reports no active work and distinguishes side work, preserved primary work, and non-lifecycle deferred concepts.

## 11. Proposed file layout and responsibilities

```text
evospec/
    __init__.py                 # public Core and Workflow exports
    domain.py                   # additive repository, intent, links, drift records
    lifecycle.py                # additive VERIFY/CONVERGE pause transitions
    persistence.py              # additive strict codecs and atomic state fields
    ports.py                     # narrow read-only discovery and caller evidence ports
    services.py                  # Core mutations, still the sole authority boundary
    workflow.py                  # WorkflowService facade and result contracts
    discovery.py                 # deterministic filesystem/content evidence
    policies.py                  # research, intake, clarification, assurance, convergence
    cli.py                       # optional thin argparse adapter for seven command concepts
tests/
    test_workflow_discovery.py
    test_workflow_intake.py
    test_workflow_clarification.py
    test_workflow_assurance.py
    test_workflow_recovery.py
    test_workflow_dogfood.py
    test_core_feature002_extensions.py
```

Keep each production module below the repository's practical 250 line module guidance where feasible. Split codecs or policy helpers if that boundary is exceeded, but do not introduce a generic framework, plugin registry, event log, or duplicate store. The plan does not modify README, ROADMAP, Constitution, or Feature 001 artifacts.

## 12. Test strategy

Use `unittest`, temporary repositories, direct byte and path inspection, and deterministic fixtures. Tests for the narrow verification executor use controlled fixture argv only; no test requires network, an LLM, an agent, wall-clock time, background work, database, server, or filesystem locks.

Core extension tests must cover valid empty initialization, idempotent init, attributed evidence, accepted Constitution authority, conflicting candidates, one `DeferredSideRequest`, rejection of a second request, terminal-side completion atomically finalizing the side outcome, restoring the primary, removing the marker, and creating persisted non-lifecycle `PendingIntake`; pre-commit failure retaining old state; successful atomic commit and reload retaining `PendingIntake`; explicit `new` or `change` materialization atomically creating normal work and consuming `PendingIntake` only after success; shared intent with linked per-capability changes; deferred linked intents with no status, phase, lifecycle, or `WorkingContext`; only one linked unit materializing as a lifecycle-bearing `Change` after a terminal predecessor, never while its predecessor is `PAUSED` or `BLOCKED`; one lifecycle per linked change; pause and resume from `VERIFY` and `CONVERGE` with the exact persisted pair; unavailable verification blocking convergence; explicit drift reconciliation; typed review record validation; strict schema decoding; lossless reload round-tripping; atomic publication failure; stale revision rejection; and preservation of prior authority.

Workflow tests must cover deterministic discovery order and fingerprints, ignored and unreadable files, weak text evidence not becoming semantic certainty, proportional research for all five classifications, existing-capability routing, primary default context, side isolation, material versus non-material clarification, superseding decisions, structured evidence boundaries, all required structured review inputs, deterministic `BLOCKED > WARN > PASS` precedence, missing-evidence blocking, review verdicts and read-only inspection, checkpoint invalidation, two-repair exhaustion, convergence guards, experiment acceptance and rejection, all four continue modes (active read/reconstruct, paused return, blocked report, and terminal report), status with no work and side work, malformed recovery and no-crash replay/history reconstruction, and agent-independent serialization.

If the optional CLI is provided, tests should invoke its parser with a testable output stream and a temporary repository, verifying only delegation to the canonical agent-independent `WorkflowService`, concise rendering, stable exit codes, and that no CLI path bypasses the facade. Direct API dogfooding must remain covered. Do not test OpenCode slash registration, chat or natural-language transport/interception, SDD-First plugin behavior, Claude or Codex integrations, or any other unavailable integration explicitly excluded.

The dogfooding test is one persisted end-to-end scenario, not a mock conversation. It must:

1. initialize an empty repository and inspect attributed discovery evidence;
2. create new work for a small EvoSpec capability;
3. start a change to an established behavior;
4. encounter and answer a material clarification;
5. start unrelated side work and persist a deferred unrelated request, while also retaining an independent capability unit as a `DeferredLinkedIntent`;
6. interrupt during implementation, materialize only the linked unit after a terminal outcome, and exercise `continue` for active read/reconstruct, paused return, blocked report, and terminal report outcomes using `continue` and `status`;
7. complete side work, assert exact primary restoration, and verify the single terminal mutation removes `DeferredSideRequest` while creating persisted `PendingIntake`; reload it, then explicitly materialize it through `new` or `change` without a delete-before-create window;
8. run review, verify, and converge with explicit product/runtime reconciliation;
9. assert immutable publication and terminal `DONE` state;
10. reload from disk with a fresh service object and prove no step depends on conversation memory.

## 13. Ordered test-first implementation sequence

This sequence is serialized at the architectural boundaries: Core domain and schema work, then persistence codec and recovery compatibility, then lifecycle and `CoreService` validation and mutations, then the shared `WorkflowService`, and finally the optional CLI. Review specifically proceeds as review domain records, codec and schema validation, `CoreService` validation and mutation, the `WorkflowService` evaluator, then tests and dogfooding. The Core steps must not be treated as unconstrained parallel work; later layers consume the validated authority established by earlier layers.

1. Add failing Core extension tests for valid empty repository state and attributed discovery evidence. Extend domain and schema records first, then make those tests pass.
2. Extend the persistence codec, strict schema decoding, atomic persistence, and recovery compatibility for the Core records, then validate the existing authoritative state reference and prior data behavior.
3. Add failing tests for one deferred request, shared intent, deferred linked intents, linked per-capability changes, and exact release after side terminal restoration. Extend `CoreService` validation and mutations without changing canonical active or preserved context authority, preserving one feature, one lifecycle, and one context per materialized change. The terminal-side completion mutation must commit the side result, restoration, and marker consumption together.
4. Add failing lifecycle tests for pause and resume from `VERIFY` and `CONVERGE`, including unavailable verification. Extend only the transition table and associated Core validation.
5. Add failing drift reconciliation tests. Extend Core records and mutations so explicit reconciliation remains separate from observation and publication.
6. Add `discovery.py` tests first for deterministic traversal, filtering, UTF-8 handling, fingerprints, evidence labels, Constitution candidates, and no semantic certainty from text matching. Implement the deterministic discovery policy.
7. Add policy tests for research ordering, proportional depth, classification, overlap routing, primary and side relation decisions, shared intent, and clarification question generation. Implement `policies.py` as pure policy code.
8. Add clarification tests for atomic material questions, stable unresolved retrieval, partial answers, zero-question completion, accepted scoped decisions, supersession, Constitution conflicts, and blocking readiness.
9. Add `WorkflowService` tests for init, new, change, clarify, side/deferred handling, progress, exact state-specific continue, and status. Implement the single facade by composing policies and Core mutations only.
10. Define typed review evidence and finding domain records, add strict codec/schema validation and lossless reload tests, then add `CoreService` validation and mutation tests before implementing the `WorkflowService` review evaluator. Cover structured evidence, deterministic review verdict precedence, missing evidence blocking, read-only explicit review, adaptive verification, unavailable verification, checkpoint invalidation, repair cap, and convergence guards without an AI reviewer or parallel review persistence.
11. If the optional CLI is included, add delegation and rendering tests, then implement the seven `argparse` subcommands with no workflow logic in the adapter. Direct API dogfooding remains possible regardless.
12. Add the persisted dogfooding vertical scenario, run the full `unittest` suite, inspect generated temporary repository shape, and correct only regressions caused by Feature 002.
13. Run type and syntax checks under CPython 3.11 or newer, verify no dependencies or prohibited runtime surfaces were introduced, run `changebudget check`, and close the active contract only after validation succeeds.

## 14. Traceability

| Plan area | Feature 002 coverage |
| --- | --- |
| One facade, optional seven-command thin CLI, agent-independent API | FR-001, FR-002, FR-005, FR-028 through FR-030, SC-001, SC-009 |
| Empty init state, discovery evidence, Constitution conflicts | FR-003, FR-004, FR-035, FR-036, SC-003, SC-009 |
| Deterministic research and internal classification | FR-006 through FR-010, FR-027, SC-002 |
| Material clarification and scoped decisions | FR-011, FR-012, FR-031, SC-003 |
| Primary continuity, one side, deferred request, shared intent, linked changes | FR-013 through FR-015, FR-033, FR-036, SC-004, SC-005, SC-011 |
| Product truth, observations, experiments, drift reconciliation | FR-016 through FR-018, FR-027, SC-008 |
| Structured deterministic review, verification, repair, checkpoint invalidation | FR-019 through FR-024, FR-034, SC-006, SC-007 |
| Unavailable verification and pause from VERIFY or CONVERGE | FR-021, FR-022, FR-034, SC-007 |
| Continue and status recovery | FR-015, FR-025, FR-026, SC-004, SC-005, SC-010, SC-011 |
| Persisted end-to-end dogfooding scenario | FR-032, FR-037, SC-001, SC-010, SC-011 |

Constitution traceability is maintained by deterministic evidence and recovery, product/runtime separation, explicit experiment handling, active feature continuity, material-only clarification, proportional process, tool responsibility separation, agent independence, and no automatic promotion of discovered rules.

## 15. Dogfooding acceptance scenario

Use EvoSpec itself as the project under test. Begin in a clean temporary checkout with no `.evospec/state.json`. Run `init` and verify a valid empty state, deterministic attributed evidence, and no active work. Use `new` to propose a small workflow capability, then use `change` against an established Core behavior, such as the status representation, and require one material clarification about the intended output semantics. Start unrelated documentation work as a side change, then submit another unrelated request and verify it is deferred rather than nested.

Interrupt during primary implementation. Create a fresh `WorkflowService` instance and use `status` and `continue` to recover the exact action, focus, progress, decisions, pending work, and verification state. Complete the side change, assert byte-for-byte or Core-defined semantic restoration of the primary context, and verify that the same terminal mutation removes `DeferredSideRequest` and creates persisted non-lifecycle `PendingIntake`. Reload, then use explicit `new` or `change` materialization to create normal work and consume `PendingIntake` only on success. Record automatic review, supply verification, demonstrate that unavailable verification blocks convergence, pause and reload from both `VERIFY` and `CONVERGE`, resume each exact persisted pair, and supply sufficient evidence. Converge with explicit drift and product decision handling, publish a new immutable Living Spec revision, and verify terminal done state. Reload again and inspect history, proving the scenario never depends on prior conversation memory.

## 16. Exclusions

This plan does not implement semantic locks or lock enforcement, ChangeBudget or change-scope enforcement, OpenCode slash registration or any other integration, chat or natural-language transport/interception, SDD-First plugin behavior, Claude or Codex integrations, an LLM runtime, semantic or vector search, build automation or a shell automation engine, network or cloud access, a database, server, background service, task queue, scheduler, filesystem locks, multi-writer support, transaction services, dashboards, authentication, accounts, version-control operations, domain-specific rules, copied Spec-Kit material, or a second workflow-state store. The local CLI is optional and, if included, is limited to the seven commands and delegates to `WorkflowService`. It permits only the explicit caller-approved argv verification execution described above.

It does not make Core execute workflow actions. It does not make text matching establish semantic certainty, automatically accept runtime truth, automatically reconcile drift, silently promote Constitution candidates, automatically accept experiments, or treat passing verification as product conformance. It does not add command concepts beyond the seven required concepts. It does not modify any file outside the implementation files and tests listed for the later implementation task. This planning task creates only `specs/002-mvp-workflow/plan.md`.
