# EvoSpec V2 Methodology Contract (E0 approved)

> **Status: product-owner approved on 2026-11-12 as the E0 method contract.** This defines intended method-level UX and record responsibilities, not a selected Core, schema, storage engine, CLI, or integration design. The command names are approved as method-level UX, not implemented commands or API specifications. E1 and implementation require separate authorization.

EvoSpec defines how project intent, decisions, tasks, acceptance, evidence, and continuity are kept understandable across interruptions. It does **not** orchestrate agents, measure or benchmark models, provide a sandbox, scheduler, network service, or Docker/runtime requirement. A host and its agents execute the work; project permission systems authorize actual effects.

The detailed desktop walkthroughs and E1 authorization boundary are in [`e0-v2-validation.md`](e0-v2-validation.md). Existing runtime documentation is retained at the end of this page and is explicitly distinguished from the approved E0 method contract.

## Core terms and boundaries

| Term | Meaning in this contract | Boundary |
| --- | --- | --- |
| **Project / Constitution** | Project identifies the durable repository/capability context. A Constitution is a short set of stable identity, goals, invariants, and limits. | Not a PRD or backlog. A missing Constitution does not require an administrative fix dossier for a clear bounded repair. Material cross-cutting rules require an attributable decision. |
| **Feature** | An agreed user outcome/value hypothesis with boundaries and a separately stated completion condition. | A clear, direct user request is explicit agreement for the outcome it states; do not require duplicate confirmation. Unstated material behavior still needs a decision. Accepting a slice or finishing its tasks does not complete the feature. |
| **Slice** | A small, useful, independently verifiable outcome through the relevant user-facing and technical layers. | A task or isolated endpoint is not a vertical slice when the behavior cannot yet be exercised end to end. Future slices are outcomes, not a waterfall of detailed future tasks. |
| **Task** | A technical step that contributes to a slice, such as changing a model, UI, or test. | Agents may adjust technical subtasks within the agreed slice. Tasks do not independently enlarge the feature or grant implementation permission. |
| **Change** | A bounded requested correction, evolution, or unrelated item, evaluated against the active feature and current authority. | A request is not automatically an accepted requirement, a scope expansion, or a focus change. |
| **Decision** | An attributable choice with its question, selected answer, rationale, scope, and any superseded decision it replaces. | Recommendations, defaults, observations, and agent guesses are not accepted decisions. Preserve prior decisions and why they were superseded. |
| **Evidence** | A traceable observation (for example, a test, inspection, playtest, or runtime result) tied to its source and applicable revision/context. | Evidence says what was observed, not what should be true. It does not by itself grant authority, establish usefulness, accept a slice, complete a feature, or publish Product Truth. |

The planning hierarchy is **Feature → Slice → Task**. Each slice is an outcome; its tasks are implementation means. Keep detail for the next slice only. Capture future work as outcomes or open questions, not speculative task plans.

### Minimum agreement for the next slice

When a material feature is clear enough to plan, a compact agreement for the *next* slice records only what is needed to implement and judge it:

- intended outcome and a user-observable example;
- in-scope behavior and explicit exclusions;
- relevant accepted decisions, constraints, and technical choices;
- objective acceptance criteria and the evidence needed to check them;
- material unknowns, risks, approvals, and effects on existing behavior.

This is semantic content, not a requirement for a new file. A clear, bounded, low-risk change can follow a lighter route without a feature or mandatory mini-spec. A material feature requires agreement before it is treated as accepted.

When the user has already expressed a clear intent and context identifies its target, do not ask for redundant confirmation; ask only for a missing material decision that could change the current slice. Treat only what the user actually stated as intent. That intent is not implementation permission and does not authorize commit, push, publication, merge, or destructive effects; those remain subject to separate current authority and host/project permissions.

## Proportional routes and materiality

Choose rigor by ambiguity, user impact, reversibility, data/security risk, and required evidence—not by file count, line count, or the label “small slice.” The method does not promise deterministic semantic classification: uncertain or conflicting evidence stays uncertain and is surfaced rather than silently forced into a route.

| Route | Use when | Minimum behavior |
| --- | --- | --- |
| **Lightweight change** | Intent is clear, bounded, low-risk, already authorized, and has objective checks. | Inspect only relevant context, make the bounded change, run proportional checks, and leave a minimal trace of outcome/evidence. No feature ceremony or new mini-spec merely for formality. |
| **Feature / material evolution** | The user outcome, scope, or accepted behavior needs a new or changed agreement. | Resolve only material decisions, record agreement and exclusions, define the next useful slice, then implement within current authorization. Do not detail all future tasks. |
| **High-risk change** | Permission/security/privacy, data integrity or migration, destructive/irreversible effects, installation/global paths, publication, or material regression is involved. | Apply the relevant explicit authority, risk review, rollback/safety plan, and stronger evidence regardless of size. A slice label cannot bypass these gates. |

### `/change` preflight

Before routing a change, inspect the active feature, relevant agreement/decisions, repository/worktree identity, observed behavior, and available evidence using authorized read-only access. Then classify its relationship and missing information:

| Finding | Route | Do not |
| --- | --- | --- |
| **A — Clear, bounded, authorized** | Proceed with the lightweight route, focused checks, and minimum trace. A small change may remain a correction inside the active slice. | Create a feature/mini-spec solely to satisfy a ritual; treat a recommendation as authorization. |
| **B — Material decision or deliverable needed** | Propose a new feature or a delta to the compatible active feature. State boundaries and choices; wait for the required attributable agreement before treating them as approved. | Auto-approve scope, pick an answer for the user, or turn the whole future architecture into a prerequisite. |
| **C — Essential fact/decision missing** | First inspect available sources read-only. If the missing item cannot be observed and changes the current slice, ask one smallest material question. Defer non-blocking unknowns. | Ask for facts already inspectable, fabricate an inspection, or write around unresolved material uncertainty. |

Relationship to the active feature is explicit: a **correction** satisfies already agreed acceptance; a **material evolution** changes accepted behavior, scope, or constraints and needs a delta/decision; **unrelated work** stays separate and does not switch focus until the user selects it. If a proposed change could materially worsen observable behavior—even if it was never written down as a requirement—surface the possible regression and ask whether to preserve, change, or defer it before making the incompatible change. Observation remains evidence, not an automatically invented requirement.

Examples that defeat size-based routing: a one-line permission change is high risk; a thousand-line mechanical rename may still be low semantic risk if reversible and bounded; a tiny deletion that removes observed offline behavior needs a material decision; passing tests do not resolve whether a game mechanic is fun. These are method examples, not deterministic classification guarantees.

## Approved E0 command behavior (not an implementation contract)

These slash-command names describe user-visible operations only. Arguments refine the operation; they never authorize extra scope. A no-prompt command uses context only when the repository/worktree and active feature are unambiguous and valid. Missing, closed, invalid, or multiple candidates produce a concise conflict and the minimum selection request; never choose the most recent feature automatically.

| E0 method command (not yet implemented) | Purpose and minimum context | Arguments | Documentary effects | Authority and no-context behavior |
| --- | --- | --- | --- | --- |
| `/specify` | Create a feature agreement or propose a delta for a uniquely identified feature. Requires the intended outcome and enough context to identify applicable project truth. | New intent or refinement is optional when explicit conversational context already supplies it. | May record the stated intent, explicit decisions, boundaries, and proposed next-slice contract. | A clear request with an identifiable target needs no redundant confirmation; ask only for a missing material decision. Does not authorize code. If intent or target is missing/ambiguous, ask the smallest question; do not turn a recommendation into accepted Product Truth. |
| `/clarify` | Resolve a currently pending material decision for the unique active feature. | The question ID may be omitted only when exactly one question is pending. The answer may be omitted: without an explicit, attributable user answer, show the pending question and options and wait. | Record the answer and decision lineage only after an explicit, attributable user answer. With no answer, make no documentary mutation, record no decision, and infer/select no option. | Answering a question authorizes only what that decision explicitly covers. If no question is pending, report status/no-op; do not invent one or choose a default. |
| `/plan` | Define a useful next slice from an agreed feature and relevant evidence. | Optional slice focus/constraint can refine the next plan. | Records the minimum next-slice outcome, acceptance criteria, evidence, and decomposed technical tasks as needed. Future slices remain outcomes only. | Agreement to a plan is not code-write permission. If feature agreement is absent or disputed, return to `/specify`/`/clarify`; do not plan against a guessed feature. |
| `/implement` | Continue work on the current authorized slice/task and its feature, with context-first recovery. | Optional task/slice selector narrows the operation. | Updates progress, completed/current/pending tasks, evidence references, and resume point as work actually occurs. | Requires current explicit implementation authority and valid scope; intent, an active feature, or a plan alone is not permission. No prompt is needed when context is unique, authority remains valid, and no material decision/evidence gate blocks. Continue successive authorized slices/tasks until the agreed feature outcome is met or a blocker appears; feature completion still requires its separate criteria and authority. No inferred commit, push, publication, merge, destructive effect, or issue closure. |
| `/status` | Report feature, slice, tasks, evidence, decisions, authority gaps, blockers, and context validity. | Optional target selects a known record. | **None. Always read-only**, including when the active index is invalid, stale, or ambiguous. | No write authority is required. Report invalid/missing/multiple context without repairing it, choosing “latest,” or changing focus. |
| `/change <intention>` | Preflight and route one bounded correction/evolution/unrelated change. | The intention identifies the requested change; optional details refine it. | Depending on the route, may leave a minimal change/evidence trace, propose a feature delta, or record a decision. | A request is not blanket permission. Read-only inspection precedes routing; execute only the exact delegated operation. If key context is missing, inspect first, then ask only if still material. |

The host's native permission prompt, project policy, and actual delegated write scope remain authoritative. `/specify`, `/plan`, prior agreement, and active selection never widen those controls. An agent may recommend options and adjust technical tasks; it cannot approve its own recommendation, invent subjective value, expand scope, or publish/merge/close work without separate authority.

## Conceptual active-feature index and canonical sources

A future index may be a small JSON reference per repository/worktree. The example uses descriptive placeholders, **not selected field names, a schema, file path, storage engine, or permission model**; no JSON file is created by this E0 document:

```json
{
  "repository_identity": "<canonical repository reference>",
  "worktree_identity": "<canonical worktree reference>",
  "active_feature_ref": "<canonical feature record reference>",
  "agreement_ref": "<accepted agreement reference>",
  "agreement_revision": "<current revision/reference>",
  "resume_slice_ref": "<current slice reference or null>",
  "resume_task_ref": "<current task reference or null>"
}
```

The index is a **pointer, not a second Product Truth or workflow database**. It must not copy feature text, tasks, task states, evidence, Product Truth, or boolean permissions. A source-of-truth map is required, but its physical stores are not chosen here:

| Information | Canonical source responsibility |
| --- | --- |
| Project identity and stable invariants | Project/Constitution source, when present; repository identity remains separately validated. |
| Accepted feature behavior and slice agreement | The attributable current agreement/specification record. |
| Decision and supersession rationale | Linked decision record with author/provenance, scope, answer, and revision. |
| Task breakdown and progress | The project’s canonical work/task record; the active index only points to the relevant slice/task. |
| Technical choices | Decision record linked to affected behavior and rationale; implementation is evidence, not the decision. |
| Test, inspection, playtest, and runtime evidence | Attributable evidence record/reference with result, environment/context, expected-vs-observed outcome, and relevant revision. |
| Authorization and change control | The actual user/project permission source, not a generated index flag. |
| Resume/focus state | A durable checkpoint/reference to current slice/task, completed/pending work, blockers, and resume point. |

Before any write, validate repository and worktree identity, feature and agreement references, current revision, authority, and concurrency state. A stale revision, conflicting active feature, mismatched identity, or duplicate candidates blocks mutation until refreshed or explicitly selected. Never use last-writer-wins or chat as the sole recovery source. Changing focus is explicit; after a side change, resume from its validated checkpoint, not from reconstructed conversation.

## Evidence, evolution, and separate acceptance gates

When a decision changes, preserve its earlier answer and reason for supersession. Reopen only tasks and evidence affected by the change, and record why each was invalidated; do not replay an entire historical chain. Code and observed behavior may reveal a regression, but do not silently turn observation into a permanent requirement. Before a material degradation of observable behavior, ask whether to preserve it, change it, or defer the incompatible work.

Keep these outcomes separate:

1. **Technical verification** — attributable checks show what worked under stated conditions. A test `PASS` is not a value judgment.
2. **Slice acceptance** — possible without a routine handoff only when objective criteria were agreed beforehand, current authority covers the work, sufficient fresh attributable evidence covers every criterion, and no material or subjective decision remains. Otherwise report unknown/blocked and request only the material decision.
3. **Feature completion** — requires the separately agreed feature-level outcome and authority. It is not inferred from a task list or one accepted slice; explicitly accepted early stopping/reprioritization can leave planned outcomes incomplete.
4. **Product Truth publication or release** — each has its own explicit authority, review, and evidence gates. Neither follows automatically from verification, slice acceptance, feature status, or a plan.

Missing, stale, mock-only, conflicting, or unattributable mandatory evidence is not a PASS. A subjective criterion such as usefulness or fun requires appropriate human/value feedback; automated tests cannot decide it. A valid objective slice may proceed independently while the parent feature stays open.

## E0 approval boundary

E0 specifies the behavior and record responsibilities only. It does not select an architecture, engine, exact schema, command count in the runtime, storage, state machine, migration, repository merger, or product implementation. Compare options against this approved contract only after separate E1 authorization. The gate and scenario matrix are recorded in [`e0-v2-validation.md`](e0-v2-validation.md).

---

## Existing published methodology (runtime baseline, not E0 implementation evidence)

> The following describes the previously published workflow documentation retained for continuity. Its presence is not evidence that the approved E0 V2 method has been implemented in runtime, verified as Product Truth, or released.

### The current documented workflow in brief

```text
intent -> research -> material clarification -> implementation
       -> REVIEW -> VERIFY -> explicit convergence -> accepted outcome
```

The documented workflow scales depth to the change. Its existing implementation vocabulary includes `TUNE`, `PATCH`, `FEATURE`, `FOUNDATION`, and `EXPERIMENT`; these are not classifications selected by this E0 contract.

### 1. Start with intent and evidence

Record what someone wants to change or create. For an established capability, research comes before changing its accepted behavior. Start with the active feature and relevant Living Specs, then inspect decisions, constraints, related capabilities, implementation, and tests when those sources are relevant.

Research is evidence led. It should preserve where a conclusion came from and how much confidence it deserves. Project documents and tests can inform a decision, but weak or conflicting discoveries do not silently become binding product rules.

When a request already matches an existing capability, treat it as a change to that capability rather than creating duplicate product truth. The broader adoption path is described in [`standalone-cli.md`](standalone-cli.md), while provider integration is documented in [`opencode.md`](opencode.md).

### 2. Clarify only material questions

Ask a question when its answer could change behavior, architecture, scope, risk, lifecycle, or product meaning. Keep each question atomic and record the accepted answer as a scoped decision. A theoretical ambiguity with no material effect does not need a clarification record.

An unresolved material decision blocks a change from becoming ready or entering implementation. Superseded decisions remain traceable, so a later choice changes the current guidance without erasing the earlier one.

### 3. Implement with a recoverable context

Once required decisions are accepted, implementation can begin with a ready context. Record the current action, focus, completed work, current work, pending work, and resume point as work changes. Recovery should restore recorded context, not invent progress or rely on an old conversation.

Unrelated work can be handled as side work. Preserve the primary context and restore it after the side change reaches a terminal outcome. Side work remains traceable, but is not silently presented as unfinished primary work.

### 4. Keep REVIEW and VERIFY separate

**REVIEW** asks whether the result fits the intent, accepted decisions, constraints, product truth, scope, and available evidence. Structured review evidence and findings produce a durable verdict such as `PASS`, `WARN`, or `BLOCKED`. An explicit review inspection is read only. Review is not a claim that code was understood autonomously.

**VERIFY** records technical or other supporting evidence and whether that evidence is sufficient for the change. Verification is not product acceptance. A passing test can show that an observed behavior works under a test condition, but it cannot decide whether that behavior is the intended product behavior.

If implementation changes after a review or verification checkpoint, the affected assurance is invalidated and fresh evidence is needed.

### 5. Converge only by explicit choice

Convergence is optional until someone explicitly requests it. It is allowed only after an acceptable review, sufficient verification, and explicit reconciliation of relevant decisions, observations, experiments, drift, and final context.

If expected behavior changed, convergence needs caller or model authored Product Truth content before a new immutable Living Spec revision can be published. This retained historical wording describes content provenance only: a caller may submit model-assisted content, but authorship or submission alone neither accepts it nor authorizes publication. The existing protocol's caller-authored `product_truth_content` requirement and explicit convergence authority are unchanged by E0 method approval. If expected behavior did not change, the change can reach an accepted terminal archive without publishing a new Product Truth revision. There is no automatic convergence.

Code, tests, runtime observations, and experiment results are evidence. They are not autonomous Product Truth publication. A rejected experiment remains useful knowledge without changing the Living Spec. An accepted experiment becomes eligible for deliberate product adoption only through an explicit decision.

### Living Product Truth

A Living Spec describes accepted expected behavior for one capability. Its revisions retain history. Runtime truth may differ from Product Truth. Record that difference as drift for reconciliation instead of silently choosing implementation behavior or specification text.

```text
What happened?       evidence, code, tests, observations
What should happen?  accepted Product Truth in the Living Spec
Who decides?         an explicit product decision at convergence
```

For the current system boundary, see [`architecture.md`](architecture.md). Related runtime guides cover [`agent-protocol.md`](agent-protocol.md), [`semantic-locks.md`](semantic-locks.md), and [`opencode.md`](opencode.md).
