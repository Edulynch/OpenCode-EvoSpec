# Agent Protocol

The agent protocol is a provider-neutral JSON boundary over `WorkflowService`. An agent sends one JSON request on standard input and receives one JSON response on standard output. The protocol is separate from the standalone lifecycle CLI and does not bypass Core.

> **E0 V2 boundary:** The proposed `/specify`, `/clarify`, `/plan`, `/implement`, `/status`, and `/change` UX in [`methodology.md`](methodology.md) is not this implemented JSON action surface or an approved API. This guide documents the existing protocol only; E0 has not selected its eventual engine or schema.

For the workflow rules, see [methodology.md](methodology.md). For OpenCode's native integration, see [opencode.md](opencode.md). For persistence and authority boundaries, see [architecture.md](architecture.md). For lock behavior, see [semantic-locks.md](semantic-locks.md).

## Starting the Protocol

Use the module form from a source checkout or the environment containing the package:

```text
python -m evospec.agent_protocol --root <repository>
```

Installed package environments also expose these entry points:

```text
evospec-agent-protocol --root <repository>
evospec-agent --root <repository>
```

Each process consumes one UTF-8 JSON request and writes one UTF-8 JSON response followed by a newline. The process accepts no arguments other than the optional `--root PATH` selector. Invalid JSON, invalid UTF-8, duplicate keys, unknown keys, and invalid field types produce a structured `INVALID_REQUEST` response. A workflow failure, including persisted authority that cannot be safely recovered, produces a structured `WORKFLOW_ERROR` response without changing state.

For example, a context request can be sent from a shell that supports standard input piping:

```text
printf '%s\n' '{"action":"context"}' | python -m evospec.agent_protocol --root <repository>
```

## Context First

Start with `context` and treat the returned EvoSpec lifecycle fields as authoritative. It includes the current `revision`, initialized `repository_id`, `initialization_state`, lifecycle, active Change and Feature identifiers, decision state, lock summaries, blockers, `legal_actions`, a fact-derived `outcome`, and a bounded `external_sdd_evidence` projection with total and completeness metadata.

```json
{"action":"context"}
```

Choose the next action from the returned `legal_actions`. Do not assume that an action is legal because it was legal in an earlier response. `context` is read-only and does not require `expected_revision`.

`outcome.kind` is exactly one of `HUMAN_MATERIAL_DECISION`, `SAFE_AUTOMATIC_OPERATION`, `BOUNDED_DELEGATION`, `EXTERNAL_WAIT`, or `STRUCTURAL_DEFECT`. It is derived from Core facts, not from an action name. `outcome.intervention` preserves the compatibility projection: `HUMAN_MATERIAL_DECISION` maps to `USER`; `SAFE_AUTOMATIC_OPERATION`, `BOUNDED_DELEGATION`, and `EXTERNAL_WAIT` map to `AUTOMATIC`; and `STRUCTURAL_DEFECT` maps to `NONE`. A `HUMAN_MATERIAL_DECISION` outcome always carries `material_decision` with one concrete decision and at least two distinct Core-offered options. Nonhuman outcomes cannot carry material-decision evidence. `BOUNDED_DELEGATION` is a typed disposition only; it does not create or persist authority. Missing decision choices are surfaced as `STRUCTURAL_DEFECT`, never as a user handoff. A terminal context has `outcome: null`; existing terminal and completion facts remain authoritative.

When `initialization_state` is `UNINITIALIZED`, `context`, `new`, and `change` return a normal success envelope whose result code is `INIT_REQUIRED`. The legal actions are exactly `init` and `context`; initialize with the returned revision before submitting intake. This state does not inspect repository capabilities or external SDD structure. Persisted authority that cannot be recovered remains a `WORKFLOW_ERROR`.

External SDD evidence contains only recognized repository-relative structure: Spec-Kit requires `.specify/` and may include `speckit.*` OpenCode commands; OpenSpec reports its recognized `openspec/` and store paths. Ordinary `specs/` directories and custom process material are not classified. The projection does not read foreign content, decide whether it is ACTIVE, HISTORICAL, or UNKNOWN, assess a conflict, or mutate state. Those remain model responsibilities. Treat foreign documents as observed and preserve them; where relevant, use the result only as an advisory contextual warning. The protocol never automatically replaces, migrates, integrates, rewrites, removes, disables, or selects foreign SDD material.

## Actions

The protocol action names are not standalone CLI commands.

| Action | Purpose |
| --- | --- |
| `context` | Return the recoverable context and legal actions. |
| `init` | Initialize EvoSpec state. |
| `new` | Submit a new intent with a typed attributable assessment. |
| `change` | Submit an established capability change with a typed assessment. |
| `clarify` | Resolve returned material questions by their returned IDs. |
| `structure_material_clarification` | Structure one exact legacy unresolved detail without answering it; requires repository, Feature, Change, and revision identity. |
| `recover_legacy_non_material_detail` | Record one exact legacy unresolved detail as a non-material Observation and remove that blocker; requires repository, Feature, Change, detail, and revision identity. |
| `continue` | Recover and continue persisted work. |
| `progress` | Record implementation progress and a resume point. |
| `assess_locks` | Submit the required assessments for active Semantic Locks. |
| `review` | Record typed product review findings and a verdict. |
| `verify` | Record typed technical evidence and whether it is sufficient. |
| `reject` | Terminalize the active work through Core; when it is side work, restore its preserved primary. |
| `converge` | Complete the explicit convergence decision after its gates pass. |
| `invalidate_assurance` | Explicitly clear stale review and verification assurance after changed implementation work. |

`new` and `change` require the full typed assessment contract. The Core determines lifecycle authority from that assessment and the persisted state. The protocol does not provide a shortcut for choosing internal classifications.

`assessment.material_ambiguities` is an array of `{ "decision": "...", "options": ["...", "..."] }` objects. Each decision must be concrete and each option nonblank and unique; at least two options are required. Plain prose is not accepted for new intake. A pending typed clarification exposes only its first question as `{id, prompt, options}` and projects the exact `decision` and `options` in `outcome.material_decision`. `clarify` accepts one answer matching the current question ID and one exact offered option. Resolving that question reveals the next one and increments `human_material_decisions` exactly once.

Historical unstructured `unresolved_decision_details` remain readable and fail closed as `STRUCTURAL_DEFECT`. Choose the explicit recovery that matches the caller's assessment; Core and adapters never classify prose. `structure_material_clarification` requires `expected_revision`, `repository_id`, `feature_id`, `change_id`, exact first `legacy_detail`, `decision`, and `options` (two or more distinct nonblank choices). It stores a typed pending clarification without choosing an answer. `recover_legacy_non_material_detail` requires only `expected_revision`, `repository_id`, `feature_id`, `change_id`, and the exact first `legacy_detail`; it accepts no replacement prose or fabricated choices. In one Core mutation it appends an auditable Observation with kind `legacy_non_material_recovery`, the exact statement, `accepted_into_truth: false`, and `outcome: null`, removes only that unique detail, and recomputes the material blocker. It does not create a Decision, change autonomy metrics or envelope, or publish Product Truth. Remaining legacy details stay `STRUCTURAL_DEFECT`; remaining typed clarifications stay `HUMAN_MATERIAL_DECISION`; when nothing material remains, the normal transition to `READY` resumes. Exact identities, current revision, order, and replay are checked. Never infer options or non-material classification from legacy prose.

## Revision Discipline

Every mutating action must include the exact `expected_revision` returned by the latest `context` or successful mutation response. The revision is a concurrency guard, not a suggestion.

```json
{"action":"init","expected_revision":0}
```

A successful response contains both `result` and the refreshed `context`, including the next revision. Use that revision for the next mutation. If the stored revision no longer matches, the protocol rejects the request without a partial mutation:

```json
{"error":{"actual_revision":4,"code":"STALE_REVISION","detail":"...","expected_revision":3}}
```

After `STALE_REVISION`, request `context` again and build the next request from its current legal actions and revision. Never edit `.evospec` to recover from a stale request.

## Rejecting Active Work

`reject` is a provider-neutral protocol action, not a standalone CLI command or a native command. Send it only when the latest `context` response includes `reject` in `legal_actions`, using the exact returned revision:

```json
{"action":"reject","expected_revision":N}
```

Successful rejection terminalizes the active work using Core's existing semantics. If the active work is side work, it restores the preserved primary through Core. It does not converge work or publish Product Truth.

## Clarification Flow

When intake identifies material ambiguity, the response includes questions with stable `id` values. A caller must use those returned IDs, not a recreated question string.

```json
{"action":"clarify","expected_revision":3,"answers":[{"question_id":"<returned-question-id>","answer":"<answer>"}]}
```

The response carries the new context and removes a question only when its answer resolves it. Reload context if more questions remain.

## Review, Verification, and Convergence

Review and verification are separate protocol actions and separate workflow gates. Record product review first, then submit technical verification when it is legal. A review response does not mean that convergence has happened.

```json
{"action":"review","expected_revision":7,"review":{"verdict":"PASS","findings":[],"safe_to_proceed":true}}
```

```json
{"action":"verify","expected_revision":8,"verification":{"evidence":["focused tests"],"sufficient":true}}
```

Stop after review and verification unless the caller explicitly requests convergence. Convergence is a separate, revision-bound action. It requires an assessment for the active Change and, when Product Truth changes, explicit caller-authored `product_truth_content`.

```json
{"action":"converge","expected_revision":9,"assessment":{"change_id":"<active-change-id>","product_truth_changed":false,"confidence":"HIGH","evidence":[{"source":"agent","reference":"tests"}],"rationale":"<explicit decision>"}}
```

When `product_truth_changed` is `true`, add nonblank `product_truth_content` to the assessment. Core guards review, verification, lock assessments, and convergence. There is no automatic convergence.

## OpenCode Boundary

The OpenCode V2 local plugin invokes this protocol through provider-neutral and typed tools. Native commands such as `/evospec.context`, `/evospec.init`, `/evospec.start`, `/evospec.status`, `/evospec.clarify`, `/evospec.review`, and `/evospec.converge` are OpenCode commands, not standalone CLI commands. They instruct an OpenCode agent to use context-first protocol actions and preserve revision discipline.

OpenCode is an integration client, not a Core dependency. The protocol remains usable by other agents and callers that can exchange the documented JSON messages.
