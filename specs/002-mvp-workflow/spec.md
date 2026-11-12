# Feature Specification: MVP Workflow

**Feature**: 002 MVP Workflow  
**Status**: Planned  
**Date**: 2026-09-03

## Overview

The MVP Workflow turns Core Bootstrap into one usable, vertical workflow for evolving product specifications. A user describes intent in natural language through a thin local `evospec` CLI or a direct API caller. The workflow discovers context, classifies the change internally, researches proportionally, asks only material questions, records a ready change, supports implementation, reviews and verifies the result, converges product truth with evidence, and records a recoverable outcome.

An optional thin local CLI is in scope with exactly these commands: `evospec init`, `evospec new`, `evospec change`, `evospec clarify`, `evospec continue`, `evospec status`, and `evospec review`. If provided, they are one adapter surface for the same workflow, not independent utilities. The shared agent-independent application API is canonical; the adapter contains no workflow authority of its own and delegates to it. Dogfooding may use this CLI or the direct API.

Core Bootstrap remains the agent independent persistence and lifecycle boundary. The workflow may decide what to ask, inspect, or record, but Core Bootstrap owns persistence, lifecycle validation, immutable Living Spec publication, and exact side context restoration. Core Bootstrap does not execute workflow actions or determine product conformance.

## Clarifications

### Session 2026-09-03

- Q: What should happen when an unrelated request arrives while a side change is already active? → A: Allow only one active side change; defer the new unrelated request until the current side change is terminal and the primary context is restored.
- Q: What should happen when required verification cannot run because the project environment or tooling is unavailable? → A: Record verification as unavailable or insufficient; keep work unfinished and allow it to be paused or rejected explicitly.
- Q: How should initialization handle conflicting Constitution candidates or discovered rules? → A: An accepted Constitution remains authoritative; conflicting candidates stay labeled evidence, and unresolved conflicts block only affected decisions.
- Q: How should one request that materially changes multiple existing capabilities be represented? → A: Preserve the user request as shared intent, then create linked per-capability changes only when their lifecycle, review, verification, or acceptance differs.
- Q: What must be demonstrated before EvoSpec replaces Spec-Kit as the primary workflow for Feature 003? → A: Require one complete persisted vertical scenario covering initialization, new work, an established-behavior change, material clarification, side work, interruption/recovery, status, review, verification, convergence, and done without relying on conversation memory.

- Q: What command surface and transport are in scope for this MVP? -> A: Include only the thin local `evospec` CLI commands `init`, `new`, `change`, `clarify`, `continue`, `status`, and `review`, plus the direct API; exclude slash registration, chat transport, natural-language interception, SDD-First plugin behavior, and Claude/Codex transport integrations.
- Q: How does multi-capability work own lifecycle state? -> A: Keep shared parent intent with lightweight deferred linked intents. Only the current unit becomes a lifecycle-bearing Core-owned `Change` and `Working context`; deferred intents have no phase, status, or context and materialize later with a shared link ID. Do not decompose unless independent lifecycle, review, verification, or acceptance differs.
- Q: What are the exact semantics of `status` and `continue`? -> A: `status` is always read-only. `continue` reads or reconstructs active non-paused context; a paused change may narrowly resume through validated Core-backed transition to its stored active phase and status; blocked work is reported without clearing, terminal work is reported without reopening, and no implementation, strategy invention, gate bypass, or automatic block or clarification resolution occurs.
- Q: What deterministic contract governs structured review? -> A: Review consumes structured `ReviewEvidence` and `ReviewFindings`; caller or integration observations are non-authoritative, automatic review is workflow evaluation rather than autonomous AI code understanding, missing inputs never pass, and verdict precedence is `BLOCKED` over `WARN` over `PASS`. Explicit review only inspects, evaluates, and reports without mutation; review remains separate from verification.
- B1 supplied decision (Session 2026-09-03): A deferred-side request exists only while its side change is active. Terminal-side completion atomically finalizes the side outcome, restores the exact primary context, removes the side marker, and transforms the request into one singular persisted Core-owned, non-lifecycle `PendingIntake`. Later `new` or `change` materialization atomically creates normal work and consumes `PendingIntake`; a pre-commit failure preserves the prior authoritative state.
- B2 supplied decision (Session 2026-09-03): A `DeferredLinkedIntent` materializes only after its active linked `Change` reaches a terminal state. `PAUSED` and `BLOCKED` units remain current and do not permit linked materialization.
- B3 supplied decision (Session 2026-09-03): `PAUSED` is permitted from `IN_PROGRESS`, `VERIFYING`/`VERIFY`, and `VERIFYING`/`CONVERGE`; resumption restores the exact persisted state, phase, focus, and context without waiving a gate or executing automatically.

## Goals

- Make the shared application API work as one vertical workflow from intent to done, with an optional thin local CLI adapter.
- Discover repository context and establish a usable EvoSpec relationship without requiring the user to know internal artifact locations.
- Keep one primary active feature, isolate unrelated side changes, and restore the primary context exactly.
- Classify changes internally as `TUNE`, `PATCH`, `FEATURE`, `FOUNDATION`, or `EXPERIMENT`, without asking the user to select a category.
- Research established behavior and relevant evidence before changing it, with deterministic depth proportional to complexity, risk, and uncertainty.
- Ask atomic clarification questions only when ambiguity materially affects behavior, architecture, scope, risk, lifecycle, or product semantics.
- Distinguish accepted product truth from observed runtime truth, including implementation, test, and experiment observations.
- Preserve rejected experiments as knowledge without changing product truth, and promote an experiment only through explicit acceptance.
- Provide automatic review and adaptive verification, plus a read-only explicit review inspection.
- Permit an optional thin local CLI adapter with exactly the seven workflow commands, while keeping the canonical behavior agent-independent and directly callable by API.
- Guard convergence so acceptance requires an acceptable review, sufficient verification, and explicit reconciliation.
- Persist enough state for exact recovery after interruption, restart, compaction, exhaustion, or agent and model change.
- Reach dogfooding readiness, so subsequent EvoSpec development can use EvoSpec as its primary SDD workflow.

## User Scenarios

### US-1: Initialize project context

**Priority**: P0

**Story**: As a project maintainer, I want EvoSpec to discover the project context and establish its artifact relationship, so later work starts from known facts.

**Acceptance scenarios**

1. **Given** a repository without EvoSpec working state, **When** `evospec init` is invoked, **Then** the workflow discovers available documentation, instructions, and Constitution candidates, labels their evidence strength, and establishes Core Bootstrap state without treating weak discoveries as binding rules.
2. **Given** an initialized repository, **When** initialization is repeated, **Then** existing accepted state and product truth remain intact and no duplicate primary feature is created.
3. **Given** no active feature, **When** initialization completes, **Then** the absence of active work is represented explicitly and no focus or progress is invented.
4. **Given** initialization state is malformed or incomplete, **When** `init` inspects it, **Then** it reports the condition without overwriting valid accepted state or modifying application behavior, dependencies, architecture, or source structure.

### US-2: Start new or established change work

**Priority**: P0

**Story**: As a contributor, I want new capability work and changes to established behavior to enter the same workflow, so intent is handled consistently.

**Acceptance scenarios**

1. **Given** a natural language request, **When** `evospec new` or `evospec change` receives it, **Then** the workflow records intent against the relevant feature or capability and does not require a user supplied classification.
2. **Given** `new` receives a capability that research identifies as already existing, **When** the overlap is established, **Then** the workflow recommends or routes to `change` rather than creating duplicate product truth.
3. **Given** `change` receives a request, **When** it researches the request, **Then** it inspects the Active Feature first, then relevant Living Specs, related project context, and relevant code or tests when the prior evidence requires them.
4. **Given** an active primary feature and a related request, **When** the request is interpreted, **Then** the primary feature is the default context unless a more specific scope decision is made.
5. **Given** an active primary feature and an unrelated request, **When** the request is interpreted, **Then** it becomes an independently identifiable side change, the complete primary context is preserved, and the primary feature remains recoverable.
6. **Given** an established capability, **When** a change is proposed, **Then** research precedes any change to its accepted behavior.

### US-3: Research and classify proportionally

**Priority**: P0

**Story**: As a contributor, I want the workflow to gather the right context without ceremony for its own sake, so decisions are reliable and effort matches change risk.

**Acceptance scenarios**

1. **Given** a proposed change, **When** research runs, **Then** it examines relevant Living Specs, decisions, constraints, rejected experiments, tests, implementation context, and related capabilities as applicable.
2. **Given** a small tuning change, **When** research completes, **Then** the workflow uses focused evidence and does not require the full depth intended for a foundational change.
3. **Given** a cross-cutting or uncertain change, **When** research completes, **Then** the workflow expands evidence and preserves the rationale for its depth and internal classification.
4. **Given** user intent that does not identify a category, **When** classification occurs, **Then** the workflow selects internal `TUNE`, `PATCH`, `FEATURE`, `FOUNDATION`, or `EXPERIMENT` metadata without presenting category selection as a prerequisite.

### US-4: Resolve material decisions

**Priority**: P0

**Story**: As a product owner, I want only consequential ambiguity brought to me, so accepted decisions are clear and reusable.

**Acceptance scenarios**

1. **Given** research finds an ambiguity that can change behavior, architecture, scope, risk, lifecycle, or product semantics, **When** the workflow reaches clarification, **Then** it asks a concise atomic question and records the accepted answer as a scoped decision.
2. **Given** multiple independent material decisions, **When** clarification is requested, **Then** each decision is distinguishable and can be answered without obscuring the others.
3. **Given** an ambiguity with no material effect, **When** the change proceeds, **Then** no clarification is required solely for theoretical ambiguity.
4. **Given** a material decision is unresolved, **When** the change would otherwise become ready or enter implementation, **Then** the workflow blocks that progression.
5. **Given** the user explicitly invokes `clarify`, **When** a material decision is available, **Then** the same persisted clarification boundary is used without creating a parallel workflow.

### US-5: Prepare and implement recoverably

**Priority**: P0

**Story**: As an implementer, I want accepted intent and constraints available before work begins, so implementation can resume without reconstructing the conversation.

**Acceptance scenarios**

1. **Given** intent is understood and all required material decisions are accepted, **When** the workflow marks the change ready, **Then** the change has a recoverable ready context and the relevant product truth and constraints are identifiable.
2. **Given** a ready change, **When** implementation begins, **Then** the workflow records current action, focus, completed work, current work, pending work, and a resume point through Core Bootstrap.
3. **Given** work is interrupted, **When** `continue` is invoked or recovery is detected, **Then** the exact persisted feature, change, phase, decisions, constraints, progress, checkpoints, and verification state are restored without invented progress.
4. **Given** unrelated side work is completed, **When** primary work is restored, **Then** the preserved primary context is reinstated verbatim and side work remains traceable but is not presented as primary unfinished work.

### US-6: Review and verify adaptively

**Priority**: P0

**Story**: As a product owner or implementer, I want implementation assessed against intent and evidence, so technical success is not mistaken for product success.

**Acceptance scenarios**

1. **Given** implementation work reaches review, **When** automatic review runs, **Then** it evaluates deterministic workflow facts; requirements and objectives; accepted clarifications; user constraints; Constitution facts; Living Spec references; completed, current, and pending work; product truth and runtime drift; caller or integration observations; and available technical evidence.
2. **Given** review has completed, **When** it records a result, **Then** structured `ReviewEvidence` and `ReviewFindings` produce one deterministic atomic verdict of `PASS`, `WARN`, or `BLOCKED`, with missing required inputs never producing `PASS`, non-authoritative external observations remaining non-authoritative, and safe-to-proceed meaning preserved without autonomous semantic code understanding.
3. **Given** a user explicitly invokes `review`, **When** the durable review view is inspected, **Then** the result is read-only and does not advance lifecycle or phase, repair work, accept the change, or alter product truth.
4. **Given** an implementation alteration occurs after review or verification, **When** the workflow records that alteration, **Then** affected checkpoints are invalidated and fresh review and verification are required.
5. **Given** a classified change, **When** verification depth is selected, **Then** evidence is calibrated to its classification, risk, and uncertainty rather than applying one fixed ceremony.

### US-7: Converge, complete, or preserve an experiment

**Priority**: P0

**Story**: As a contributor, I want completion to reconcile intent with evidence, so the Living Spec reflects deliberate product decisions rather than accidents.

**Acceptance scenarios**

1. **Given** review is `PASS`, or is `WARN` explicitly marked safe to proceed, and verification is sufficient, **When** convergence is confirmed, **Then** the workflow may reconcile product truth, runtime observations, decisions, experiment outcomes, final state, drift, and primary context restoration.
2. **Given** review is `BLOCKED`, unsafe `WARN`, or verification is insufficient, **When** convergence is attempted, **Then** acceptance is guarded and the change remains unfinished or is rejected according to an explicit decision.
3. **Given** an accepted change changes expected behavior, **When** convergence reaches an explicit product decision, **Then** Core Bootstrap publishes a new immutable authoritative Living Spec revision and retains prior history.
4. **Given** runtime truth differs from product truth, **When** convergence encounters the difference, **Then** it surfaces unresolved drift and does not silently choose either source.
5. **Given** an experiment is rejected, **When** it completes, **Then** its outcome remains distinguishable knowledge and does not change product truth.
6. **Given** an experiment is accepted, **When** acceptance is explicit, **Then** its intended result is eligible for deliberate incorporation into product truth.
7. **Given** convergence is complete, **When** the change reaches done, **Then** it is terminal, no longer unfinished work, and its accepted product truth and evidence remain traceable.

### US-8: Inspect and recover exact state

**Priority**: P0

**Story**: As a contributor returning after interruption, I want a different agent or conversation to recover exact work state, so continuity does not depend on LLM memory.

**Acceptance scenarios**

1. **Given** persisted work exists, **When** `status` is inspected, **Then** it identifies the primary feature, current change, focus, phase, action, decisions, constraints, progress, review result, verification state, and resume point.
2. **Given** a side change is active, **When** status or `continue` is used, **Then** active side context and preserved primary context are both identifiable and cannot be confused.
3. **Given** state is missing, corrupt, stale, or inconsistent, **When** recovery is attempted, **Then** the workflow reports recovery unavailable or blocked and never fabricates progress or product truth.
4. **Given** no work is active, **When** status is inspected, **Then** it reports no active work explicitly.
5. **Given** a paused, blocked, or terminal change, **When** `continue` is invoked, **Then** it resumes only the permitted paused state, or reports blocked or terminal state without clearing or reopening it.

## Edge Cases

- A request overlaps an existing capability but its relationship is uncertain. The workflow records the uncertainty and clarifies only if the choice materially changes scope or behavior.
- A request materially affects multiple capabilities. The workflow stores shared parent intent and lightweight deferred linked intents. Only the current unit has a Core-owned lifecycle-bearing `Change` and `Working context`; deferred intents have no phase, status, or context and later materialize with a shared link ID. The workflow does not decompose work when lifecycle, review, verification, and acceptance are shared.
- A deferred-side request arrives while another side change is active. The workflow persists one side-change-specific deferred request until the active side change reaches a terminal outcome, then atomically finalizes the side outcome, restores the exact primary context, removes the side marker, and transforms the request into `PendingIntake`; it does not create nested side work, a queue, or a scheduler, or overwrite the preserved primary context.
- A material decision is superseded. The newer decision becomes current while the earlier decision remains traceable.
- A review warning is present without an explicit safe-to-proceed decision. Convergence is blocked.
- Verification passes technically while product truth is not satisfied. The workflow reports the distinction and does not accept automatically.
- Required verification cannot run because tooling or the project environment is unavailable. The workflow records unavailable or insufficient evidence, blocks convergence, and keeps work unfinished until it is paused or explicitly rejected.
- Two repair iterations have been recorded. The workflow preserves a blocker and safe next step before pausing; it does not start a third repair iteration.
- A terminal change is encountered during recovery. It remains traceable but cannot be resumed as unfinished work.
- A publication or state update fails. The prior authoritative state and Living Spec publication remain authoritative; an unreferenced publication is not treated as product truth.
- An implementation observation or experiment result conflicts with accepted product truth. The observation remains runtime truth until an explicit product decision reconciles it.
- Discovered project principles have weak evidence. They remain provisional and cannot silently become constitutional rules.
- Constitution candidates conflict. The accepted Constitution remains authoritative; conflicting candidates remain attributed evidence and block only a decision materially affected by the conflict until the user resolves it.

## Functional Requirements

1. **FR-001 [P0]** If the optional local CLI is provided, its surface MUST comprise exactly these seven commands: `evospec init`, `evospec new`, `evospec change`, `evospec clarify`, `evospec continue`, `evospec status`, and `evospec review`.
2. **FR-002 [P0]** The shared agent-independent application API MUST be canonical and MUST converge on one workflow. If the optional local CLI is provided, its seven commands MUST be thin adapters over that API and MUST NOT create incompatible independent state or lifecycle paths; dogfooding MUST be possible through the direct API.
3. **FR-003 [P0]** `init` MUST discover available repository documentation, instructions, and Constitution candidates, label evidence strength, and establish the repository relationship through Core Bootstrap.
4. **FR-004 [P0]** `init` MUST detect malformed or incomplete EvoSpec initialization state and report it without overwriting valid accepted state or modifying application behavior, dependencies, architecture, or source structure.
5. **FR-005 [P0]** `new` and `change` MUST accept natural language intent and MUST NOT require user classification selection.
6. **FR-006 [P0]** `new` MUST research existing capabilities and recommend or route to `change` when the requested capability already exists, rather than silently creating duplicate product truth.
7. **FR-007 [P0]** `change` MUST research the Active Feature first, then relevant Living Specs, related project context, and relevant code or tests when needed.
8. **FR-008 [P0]** The workflow MUST classify each change internally as `TUNE`, `PATCH`, `FEATURE`, `FOUNDATION`, or `EXPERIMENT` and MUST adapt research, review, verification, and convergence depth accordingly.
9. **FR-009 [P0]** The workflow MUST research relevant established product truth, decisions, constraints, rejected experiments, tests, implementation context, and related capabilities before changing established behavior.
10. **FR-010 [P0]** Research MUST use deterministic project-native discovery sufficient for the MVP, be inspectable and testable, and remain proportional to change complexity, risk, and uncertainty.
11. **FR-011 [P0]** The workflow MUST ask clarification only for material ambiguity and MUST persist accepted material answers as scoped decisions through Core Bootstrap.
12. **FR-012 [P0]** A change MUST NOT become ready or enter implementation while a required material decision is unresolved.
13. **FR-013 [P0]** The workflow MUST preserve one primary active feature and MUST represent unrelated work as a linked side change without losing the primary context.
14. **FR-014 [P0]** Side-change activation and restoration MUST use Core Bootstrap exact context preservation and restoration; the workflow MUST NOT merge, recompute, default, omit, or invent restored progress.
15. **FR-015 [P0]** The workflow MUST persist active feature, change, focus, phase, action, decisions, constraints, completed work, current work, pending work, review state, verification state, and resume point outside LLM conversation memory.
16. **FR-016 [P0]** The workflow MUST distinguish accepted product truth from runtime truth, including test, implementation, and experiment observations.
17. **FR-017 [P0]** Runtime observations, implementation accidents, and rejected experiments MUST NOT change product truth without explicit user intent or product decision.
18. **FR-018 [P0]** Accepted product truth MUST be published through Core Bootstrap as an immutable Living Spec revision, with the authoritative revision selected by persisted state.
19. **FR-019 [P0]** Automatic review MUST consume structured `ReviewEvidence` and `ReviewFindings` inputs sourced from deterministic workflow facts; requirements and objectives; accepted clarifications; user constraints; Constitution facts; Living Spec references; completed, current, and pending work; product truth and runtime drift; caller or integration observations; and available technical evidence. It MUST produce one durable atomic verdict of `PASS`, `WARN`, or `BLOCKED`; caller or integration observations remain non-authoritative, automatic review is workflow evaluation rather than autonomous semantic code understanding, missing required inputs MUST NOT produce `PASS`, and verdict precedence MUST be `BLOCKED` over `WARN` over `PASS`.
20. **FR-020 [P0]** Explicit `review` inspection MUST be read-only and MUST only inspect, evaluate, and report. It MUST NOT advance lifecycle or phase, repair work, accept a change, alter product truth, or mutate review inputs, findings, checkpoints, or other persisted state.
21. **FR-021 [P0]** Verification MUST preserve evidence and sufficiency separately from product-conformance review, and its depth MUST adapt to classification, risk, and uncertainty.
22. **FR-022 [P0]** Convergence MUST be guarded by an acceptable review, sufficient verification, and explicit reconciliation of product truth, runtime truth, decisions, experiments, drift, final state, and context restoration.
23. **FR-023 [P0]** A review or verification checkpoint MUST be invalidated when implementation is altered after that checkpoint.
24. **FR-024 [P0]** Repair MUST be limited to two persisted iterations; at exhaustion, the workflow MUST preserve a blocker and safe next step before recording paused work.
25. **FR-025 [P0]** `continue` MUST read or reconstruct exact persisted active non-paused context without mutation and report the primary feature, current focus, lifecycle phase, current work, pending work, clarifications, constraints, review facts, verification facts, convergence facts, and next safe action. For paused work it MAY perform only validated Core-backed resumption to the stored active phase and status. For blocked work it MUST report the block and required next decision or action without clearing; for terminal work it MUST report without reopening. It MUST NOT implement, invent strategy, bypass gates, or automatically resolve blocks or clarifications.
26. **FR-026 [P0]** `status` MUST always be read-only and MUST report an atomic persisted view of the active feature, phase, current work, pending work, side-change state, and blocked state without fabricating active work, progress, decisions, or product truth.
27. **FR-027 [P0]** The workflow MUST preserve accepted, rejected, abandoned, superseded, and experimental history without presenting non-current records as current product truth.
28. **FR-028 [P0]** Core Bootstrap MUST retain ownership of persistence, lifecycle validation, immutable Living Spec publication, and exact side-context restoration.
29. **FR-029 [P0]** Core Bootstrap MUST remain a caller-controlled boundary and MUST NOT execute commands, invoke agents, perform research, review, verification, convergence, repair, lock enforcement, or product reconciliation.
30. **FR-030 [P0]** Product intent, decisions, Living Specs, continuity, and workflow state MUST remain independent of any agent, model, integration, vendor, conversation, domain, or interaction surface.
31. **FR-031 [P1]** The workflow MUST communicate status, intent, ambiguity, progress, conflicts, and decisions in concise atomic units, expanding explanation only for material ambiguity, impact, risk, or user choice.
32. **FR-032 [P1]** The completed vertical workflow MUST be suitable for dogfooding EvoSpec development as the primary SDD workflow rather than Spec-Kit.
33. **FR-033 [P0]** The workflow MUST permit no more than one active side change. A new unrelated request during active side work MUST be persisted as one side-change-specific deferred request. When the active side reaches a terminal outcome, the workflow MUST atomically finalize the side outcome, restore the exact primary context, remove the side marker, and transform the request into one singular Core-owned, non-lifecycle `PendingIntake`, not a `Change`, `WorkingContext`, or `Active Feature`. `PendingIntake` MUST NOT execute automatically or use a queue or scheduler; later explicit `new` or `change` intake MUST atomically materialize normal work and consume it, while a pre-commit failure preserves prior authoritative state.
34. **FR-034 [P0]** When required verification is unavailable because tooling or the environment cannot provide it, the workflow MUST record verification as unavailable or insufficient, MUST block convergence, and MUST keep the change unfinished until it is paused or explicitly rejected.
35. **FR-035 [P0]** Initialization MUST preserve an accepted Constitution as authoritative. It MUST retain conflicting Constitution candidates as attributed evidence, MUST NOT promote them automatically, and MUST block only decisions materially affected by the unresolved conflict.
36. **FR-036 [P0]** When one user request materially affects multiple capabilities, the workflow MUST preserve shared parent intent and `DeferredLinkedIntent` records. Each lifecycle-bearing `Change` MUST have exactly one valid Core-owned lifecycle owner and exactly one valid Core-owned `Working context`. Only the current unit MAY become lifecycle-bearing; a `DeferredLinkedIntent` MUST have no phase, status, or context and MAY materialize only after the active linked `Change` reaches a terminal state, retaining a shared link ID. Its materialization MUST atomically create normal work and consume the deferred intent; a pre-commit failure MUST preserve the prior authoritative state. `PAUSED` and `BLOCKED` units remain current and MUST NOT permit linked materialization. This model MUST introduce neither a second state authority, ownerless lifecycle entities, duplicate `Working contexts`, nor a general-purpose queue or scheduler. The workflow MUST NOT decompose work unless lifecycle, review, verification, or acceptance differs.
37. **FR-037 [P0]** Dogfooding readiness MUST be demonstrated by one persisted vertical scenario that covers initialization, new work, a change to established behavior, material clarification, a side change, interruption and recovery, status inspection, review, verification, convergence, and done without relying on conversational memory.

## Key Entities

- **Repository context**: The discovered project facts and evidence labels that establish the EvoSpec relationship.
- **Living Spec**: The accepted expected behavior of one capability, published immutably and deliberately evolved.
- **Feature**: A unit of primary or side work linked to relevant product truth.
- **Change**: The current lifecycle-bearing unit of intent and work concerning a feature or capability, including internal classification, lifecycle, decisions, progress, evidence, and outcome, owned durably by Core Bootstrap.
- **DeferredLinkedIntent**: A lightweight future unit under shared parent intent, identified by a shared link ID and not yet carrying a phase, status, or working context. It materializes only after the active linked `Change` reaches a terminal state, atomically creating normal work and consuming the intent.
- **PendingIntake**: The singular persisted, Core-owned, non-lifecycle record created from a terminal side change's deferred-side request. It carries only stable identity, original intent, deferral provenance, known target hints, and an optional completed-side relationship. It is not a `Change`, `WorkingContext`, or `Active Feature`, has no lifecycle phase, status, independent review/verification/convergence lifecycle, or automatic execution, and is not a queue or scheduler. Later explicit `new` or `change` materialization atomically creates normal work and consumes it; a pre-commit failure preserves prior authoritative state.
- **Working context**: The canonical active or preserved state needed to identify and resume the current lifecycle-bearing `Change`.
- **Decision**: An accepted material clarification or user constraint, with scope and supersession history.
- **Observation**: Runtime, test, implementation, or experiment evidence that is not product truth by default.
- **ReviewEvidence**: Structured, persisted inputs from deterministic workflow facts; requirements and objectives; accepted clarifications; user constraints; Constitution facts; Living Spec references; completed, current, and pending work; product truth and runtime drift; caller or integration observations; and available technical evidence. Caller and integration observations are non-authoritative.
- **ReviewFindings**: Structured review findings with severity, affected requirement or evidence, rationale, and safe-to-proceed meaning used to derive the verdict without autonomous semantic code understanding.
- **Review result**: A durable `PASS`, `WARN`, or `BLOCKED` verdict with structured findings and deterministic `BLOCKED` over `WARN` over `PASS` precedence.
- **Verification evidence**: Evidence and sufficiency supporting the change, distinct from product-conformance review.
- **Drift**: An explicit unresolved difference between accepted product truth and runtime truth.
- **Experiment outcome**: An explicit accepted or rejected result that preserves the decision trail and controls whether product truth may change.
- **Recovery state**: Persisted active and preserved contexts, checkpoints, lifecycle data, and resume information available without conversation memory.

## Core Bootstrap Boundary

The MVP Workflow is responsible for orchestration decisions and user-facing communication. It may discover context, classify intent, select proportional research and verification depth, identify material ambiguity, request user decisions, inspect implementation evidence, produce structured `ReviewEvidence` and `ReviewFindings`, evaluate review results deterministically, and decide when convergence is justified. Automatic review evaluates supplied workflow evidence and does not autonomously understand code.

Core Bootstrap is responsible for the durable authority beneath those decisions. It owns:

- persistence and recovery-safe state publication;
- lifecycle and status/phase validation;
- immutable Living Spec publication and authoritative publication references;
- decision, observation, drift, checkpoint, review, verification, and repair state supplied by the workflow;
- exact preservation and restoration of the primary context during side changes.

The boundary is explicit. Core Bootstrap does not discover projects, classify changes, ask questions, invoke agents, run research or implementation, review behavior, gather verification evidence, repair work, reconcile product/runtime truth, enforce locks, enforce change scope, or advance work because a result appears favorable. The workflow supplies those facts and requests validated persistence.

## Non-Goals

This feature does not define or implement:

- semantic locks, lock enforcement, lock authorization, or lock conflict resolution;
- OpenCode integration, plugins, skills, adapters, or any agent-specific integration;
- ChangeBudget integration, change-scope enforcement, or change allowance measurement;
- a runtime dependency on Spec-Kit or copied Spec-Kit materials;
- semantic or vector search;
- cloud, network, background, server, database, or transaction services;
- external integrations or vendor-specific behavior;
- dashboards, accounts, authentication, billing, marketplace, or distribution features;
- a particular programming language, framework, storage technology, schema, dependency, or implementation architecture;
- OpenCode slash registration, chat transport, natural-language interception, SDD-First plugin behavior, and Claude/Codex transport integrations;
- implementation code, generated code, task plans, test plans, build behavior, or deployment behavior;
- automatic acceptance of runtime truth, experiment results, or implementation accidents;
- automatic reconciliation that bypasses explicit product decisions;
- ownership of version-control history or change-control policy;
- a domain-specific vocabulary or assumptions about mobile, web, backend, game, library, or tooling projects.

## Measurable Success Criteria

- **SC-001**: In a clean repository, one end-to-end exercise can use the local `evospec` CLI or direct API to move from initialization through new or change intent, research, clarification when material, ready, implementation, review, verification, convergence, and done without switching to an unrelated workflow.
- **SC-002**: In a test set containing at least one `TUNE`, one normal feature, one foundational change, and one experiment, research and evidence depth differ according to classification and risk, while users never select the classification.
- **SC-003**: In a set of material and non-material ambiguities, every material ambiguity produces an answerable persisted decision and no non-material ambiguity produces a required clarification record.
- **SC-004**: An interruption and restart exercise recovers 100 percent of the persisted active feature, change, phase, action, decisions, constraints, progress, review state, verification state, and resume point, with no invented progress.
- **SC-005**: A side-change exercise restores the primary context byte-for-byte or semantically exactly according to Core Bootstrap authority, with no lost or newly invented progress.
- **SC-006**: Review inspection leaves lifecycle, phase, repair count, checkpoints, product truth, review inputs, findings, and persisted state unchanged, while automatic review deterministically records exactly one atomic verdict, never returns `PASS` with missing required inputs, and applies `BLOCKED` over `WARN` over `PASS` precedence.
- **SC-007**: Convergence is rejected for blocked or unsafe review and insufficient verification, and succeeds only after acceptable review, sufficient evidence, and explicit reconciliation.
- **SC-008**: Accepted changes create a new authoritative immutable Living Spec revision; rejected experiments and unresolved runtime drift leave accepted product truth unchanged.
- **SC-009**: The same workflow records at least three unrelated software-domain examples without adding domain-specific rules or vocabulary requirements.
- **SC-010**: A dogfooding readiness exercise can use the workflow to carry subsequent EvoSpec work from intent to accepted product truth while preserving recoverable state across an agent or model change.
- **SC-011**: The dogfooding readiness exercise covers initialization, new work, a change to established behavior, material clarification, side work, interruption and recovery, status, review, verification, convergence, and done from persisted state alone.

## Assumptions

- Core Bootstrap from Feature 001 is available as the persistence, lifecycle, immutable publication, and side-context authority described here.
- A caller or future integration can provide repository access and implementation or execution observations when those observations are needed for review and verification.
- The workflow is single-project in focus at a time and maintains no more than one primary active feature.
- The user remains the authority for material product decisions, explicit experiment acceptance or rejection, and reconciliation when product truth and runtime truth differ.
- Internal classifications are workflow metadata, not user-facing prerequisites or constitutional rules.
- Research and verification can be deterministic enough to produce inspectable inputs, outputs, and outcomes without requiring cloud, network, or background services.
- Change scope and allowed files, when a project has such controls, remain owned by those controls rather than by EvoSpec.
- The local CLI and direct API remain independent of any particular agent or transport.
- The thin local CLI and direct API are the only MVP interaction surfaces; future interaction layers are not part of this feature.
- Review callers can provide structured evidence and findings, but caller or integration observations are not authoritative and cannot override persisted product truth or deterministic review precedence.
