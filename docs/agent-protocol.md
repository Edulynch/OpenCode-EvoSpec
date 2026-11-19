# Agent Protocol

The agent protocol is a provider-neutral JSON boundary over `WorkflowService`. An agent sends one JSON request on standard input and receives one JSON response on standard output. The protocol is separate from the standalone lifecycle CLI and does not bypass Core.

> **E0 V2 boundary:** The E0-approved `/specify`, `/clarify`, `/plan`, `/implement`, `/status`, and `/change` method-level UX in [`methodology.md`](methodology.md) is not implemented by this JSON action surface and is not an approved API. This guide documents the existing protocol only; E0 selected no engine or schema.

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

### E1 accepted Task: guarded operational progress (Slice C)

An explicitly approved E1 Slice is a different authority boundary from legacy `progress`. The immutable `accepted_slice` object continues to show the human-approved Feature, Slice, first Task and **initial** checkpoint. The optional `task_progress` object reports the **current operational** checkpoint with `report_kind: "REPORTED_UNVERIFIED"`; it never marks that checkpoint verified or changes the original agreement. For compatibility, `progress.resume_point` is derived from `task_progress.checkpoint` on E1 reads, but it is not written as a second source of truth.

Once `e1_schema_ready: true`, `legal_actions` may offer `accepted_task_progress` instead of `progress` for the accepted Task. Send the exact current repository ID, Task ID and agreement references returned by `context`, plus the current `revision`:

```json
{
  "action": "accepted_task_progress",
  "expected_revision": 2,
  "repository_id": "<context.repository_id>",
  "task_id": "<accepted_slice.task_id>",
  "feature_agreement_ref": "<accepted_slice.feature_agreement_ref>",
  "slice_agreement_ref": "<accepted_slice.slice_agreement_ref>",
  "checkpoint": "Local task list renders",
  "statement": "Implementation progress observed; verification remains pending",
  "evidence_refs": ["tests/task-list.test.ts"]
}
```

Core checks the exact local root and accepted Task, current focus, reversible `progress` authority, operation budget and revision **under one writer lock**. Successful reports advance the lifecycle through legal in-memory transitions and publish one state revision. The report has source kind `UNKNOWN` and no host attestation: another process owned by the same trusted local OS user can also call the provider-neutral protocol, but cannot bypass Core scope checks. A stale request must be inspected again, never automatically rebound or replayed.

Accepted E1 projects persisted in schema 13 remain **read-only** until an actual human uses the local interactive CLI command `evospec --project <repository> upgrade-e1-schema --expected-revision <current-revision>`. The prompt binds the prior state SHA-256, root identity, Task and agreements to an exact confirmation token. Conversion writes schema 14 once and retains an in-state acceptance receipt; it does not create Task progress. A recovered already-committed conversion is recognized without a second write. Non-E1 schema 13 continues writing schema 13, and compatible older non-E1 writes promote at most to 13.

Schema 14 remains readable and writable for accepted Task-progress operations; existing Task reports and E1 conversion receipts retain their identities and original records. A separately confirmed Slice-criteria amendment can move schema 14 to schema 15 atomically with its new criterion head. Newly initialized repositories use schema 15. Neither transition repeats Feature/Slice acceptance, and schema 15 still reports Task progress as unverified.

### R3-A: objective evidence for one accepted criterion

Schema 15 remains readable and writable. It **never upgrades silently** to schema 16. With the exact current revision, a human may run `evospec --project <repository> upgrade-r3-schema --expected-revision <revision>` in an interactive terminal, read the source-state SHA-256 bound decision, and type the exact `ACEPTO <token>` confirmation. Core records the immutable conversion receipt and advances one revision; an already committed conversion can be inspected without repeating the write.

A criterion is eligible for automated test evidence only when its *human-accepted* `expected_evidence` is exactly `python-unittest:tests.test_module_name` or `node-test:tests/path.test.mjs`. The dedicated `evospec_criterion_test` OpenCode tool takes the current `revision`, `repository_id`, `slice_agreement_ref`, `criteria_revision_id`, `criterion_id`, `test_kind` and `test_target` from canonical context. It verifies that the descriptor matches the current accepted head, asks OpenCode's host permission to execute the exact argv in the executing session root, checks the scope again, runs the deterministic test without a shell, and records the real exit code and bounded output SHA-256 through one revision-bound Core CAS. No stale retry is allowed. The generic `evospec` tool refuses that mutating action.

Read-only context now exposes `r3_schema_ready` and `criterion_verification` by criterion: `UNVERIFIED` until a matching test receipt exists, `VERIFIED` only for a zero-exit result, `FAILED` for a nonzero exit. Evidence references an immutable receipt, command kind/target, exit code and output digest, and explicitly identifies its operational provenance as `HOST_REPORTED_TEST`. On a material criteria amendment, old test receipts remain for audit but do **not** verify the new current head. Unsupported or subjective evidence remains `UNVERIFIED`; neither R3 result changes global `verification_sufficient`, accepts the Slice/Feature, publishes Product Truth, nor initiates a release. This is local-host operational provenance within the trusted-user boundary, **not** cryptographic proof against forged direct protocol clients or malicious tests; never treat it as independent remote attestation.

This gate verifies logical atomicity and process recovery. Physical power-loss durability on Windows remains **not certified**, because directory `fsync` is not implemented there; after a post-replace uncertainty, inspect the recovered state before taking another action.

### R3-B: separately authorized objective Slice outcome

Schemas 13–16 remain readable under their existing migration and agreement rules; schema 16 retains R3-A's evidence-only behavior and never upgrades silently to schema 17. The 16→17 migration is a separate, explicit, single-revision human decision. Before running it, create a project-relative JSON coverage proposal, for example `r3b-coverage.json`:

```json
{
  "version": "r3-known-input-contract-v1",
  "criteria": [
    {
      "criterion_id": "AC-1",
      "test_kind": "python-unittest",
      "test_target": "tests.test_module",
      "known_inputs": [
        {"kind": "root-file", "path": "tests/test_module.py"},
        {"kind": "root-directory", "path": "tests/fixtures"}
      ]
    }
  ]
}
```

List every current criterion exactly once, with its exact human-accepted test descriptor and an explicit `known_inputs` array. Each known input is an exact normalized root-relative regular-file or directory path; an empty array is permitted only as an explicit, TTY-approved declaration. The CLI rejects absolute paths, traversal, managed paths, symlinks/reparse points and non-regular files for `--coverage-file`. The proposal file itself must be inside the selected project and is part of the whole-root fingerprint; changing or removing it later makes approval stale.

Run `evospec --project <repository> upgrade-r3-outcome-schema --expected-revision <revision> --coverage-file r3b-coverage.json` in a real interactive terminal and confirm the exact displayed statement. The approval binds schema-16 source bytes, repository identity, accepted Slice and criteria head, `r3-root-input-v2` policy, admission and environment-profile policies, the canonical per-criterion coverage proposal, and the complete root fingerprint. The migration receipt and initial coverage approvals commit in one +1 CAS. It does not repeat Feature/Slice acceptance.

The fixed `r3-root-input-v2` policy scans every regular file and directory below the canonical repository root, including ignored and untracked entries. Files bind normalized relative paths, size, executable bit, and content digest; directories bind normalized relative paths and presence, so adding or removing an empty directory changes the fingerprint. It excludes only root `.git` and `.evospec`. Symlinks, reparse points/junctions, hard links, special or unreadable entries, path collisions, concurrent changes, and inputs beyond 50,000 files, 100,000 entries, 1 GiB, or 128 path levels fail closed. The read-only `context` projection exposes the exact current snapshot and policy; the OpenCode adapter does not duplicate or author the workspace fingerprint.

With schema 17 enabled, `evospec_criterion_test_r3b` runs only the exact human-accepted Python unittest-module or Node test-file descriptor, with an exact executable path/hash/version and fixed argv. Each current criterion needs a current immutable TTY-approved known-input contract bound to its Slice, criteria head, exact descriptor, policy, and whole-root fingerprint. Any root change, including directory-presence changes, stales all those approvals and test evidence; `renew-r3b-coverage --expected-revision <revision> --coverage-file r3b-coverage.json` requires a separate interactive approval, after which every criterion must be tested again. A fresh test never renews coverage. The plugin binds permission to the root, repository, accepted Slice/head/criterion, exact coverage approval, policies, runtime and environment, then rechecks requester, session root, scope, policies, coverage and fingerprint after permission and after the process; it submits no result if requester or root changes after the test. Because OpenCode V2's ordinary permission policy may `allow` custom actions silently, the plugin registers a permission `evaluate` hook that requires `ask` for R3-B criterion tests and Slice outcomes and refuses these actions if the hook is missing. The request includes the canonical root separately from the structured contract, plus metadata for the permission UI. **A real host session must still demonstrate that the client displayed the exact root and obtained a human decision; the plugin cannot attest to what the human saw.** Core independently rescans inside the result-recording lock and rejects missing, stale, unsupported, or incomplete coverage/provenance. The child environment contains only the runtime directory in `PATH`, fixed Python/TZ settings, the repository root as `TEMP`/`TMP`/`TMPDIR`, and on Windows only `SystemRoot`/`WINDIR` when present. It omits ambient home directories, credentials, user configuration and every other inherited variable. Known external, credential, excluded-managed, unprofiled-environment, and unsupported-metadata dependencies are unsupported.

This evidence is **bounded trusted-local operational evidence**, not an observed-read closure, OS sandbox or universal proof about arbitrary test code. A same-user client or test may hide filesystem/network reads or otherwise misrepresent its execution; declarations are human-approved leads, not instrumentation of actual reads. The root fingerprint covers repository files and directory presence only; it does not hash arbitrary host state, the OS, runtime libraries, network services, or reads of excluded managed `.git`/`.evospec` trees. Do not describe R3-B as proving those inputs closed. Unsupported or unverifiable runtimes/profiles and missing, stale, or unsupported known-input contracts fail closed; the accepted test descriptor remains human-authorized scope, not a claim generated by the model.

R3-A `criterion_test_result` remains separate and evidence-only, including after schema 17; legacy receipts without R3-B provenance never qualify for automatic acceptance. The dedicated `evospec_accept_slice_outcome` OpenCode tool performs a second native permission check bound to the current root, repository, accepted Slice/head, revision, policy and input fingerprint. Core then derives the accepted scope, requires a latest successful fresh supported R3-B receipt for every current criterion, rejects pending material decisions and unresolved relevant drift, rescans under its writer lock, and appends one immutable Slice outcome receipt in a separate +1 CAS. It never changes global verification, Feature status, Product Truth, another Slice, lifecycle state, or activation. `ALREADY_ACCEPTED` returns the canonical receipt without a write; stale expected revisions are rejected, not rebound.

Read-only context exposes `slice_outcome` as `BLOCKED`, `READY`, `CURRENT`, or `STALE`, with blockers and the latest historical receipt. Changes to the accepted criteria head, covered workspace inputs, or relevant drift make a prior outcome historical/stale; they do not rewrite or delete its receipt. Neither the R3-A nor R3-B test-recording action accepts the Slice.

### R6-A Feature completion (read-only agent boundary)

The agent context additionally exposes `feature_completion` for an unambiguous primary Feature: `feature_id`, `status` (`BLOCKED`, `READY_FOR_HUMAN_DECISION`, or `COMPLETE`), `receipt_id`, `blockers`, and exact `slice_agreement_refs`. The projection comes from canonical Core state; it does not infer completeness from the R4-B/R5-C truncated Slice index or a historical receipt flag. When a Slice's current evidence cannot be established under the same input snapshot, Core returns an explicit blocker rather than a speculative completion.

**No writable Feature-completion agent protocol action exists.** The human must invoke the separate CLI `evospec complete-feature` from a genuine terminal, review the Feature-level condition and exhaustive Slice/outcome basis, and enter the exact `ACEPTO <token>`. That operation may explicitly migrate a schema-17 consumer state to schema 18 in the same approved atomic transaction; reading status never migrates a project. The immutable Feature receipt is independent of Slice outcomes, lifecycle terminal archives, Product Truth, Git merge and release. New Slices against a closed Feature remain blocked pending a separate reopening decision.

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

## R6-B Feature cycles and historical Slice evidence (schema 19)

The read-only `feature_completion` projection now includes `cycle_id`, `cycle_state` (`OPEN` or `CLOSED`), `reopen_receipt_id` and a chronological `history` of reopen/completion receipts. The CLI `status` and agent context use the same Core eligibility projection; neither may migrate state or select work.

**No agent writer reopens a Feature.** The human must use `evospec reopen-feature` in a real terminal with an exact prior closure receipt, reason, purpose and revision, then confirm `ACEPTO <token>`. The atomic schema 18→19 conversion appends an immutable `FeatureReopenReceipt` while preserving schema-18 closures. It does not create or activate a Slice; `accept-next-slice` has its own human gate.

When the new cycle is open, an **accepted terminal historical Slice** may receive fresh coverage via the TTY command `renew-r3b-coverage --slice-agreement-ref`. The existing OpenCode `evospec_criterion_test_r3b` tool can then execute its exact approved descriptor **only with real host permission** and Core's archived-scope/fingerprint validation. The separate `evospec_accept_slice_outcome` tool takes optional `slice_agreement_ref` for this case. Its provider-neutral action is `accept_historical_slice_outcome` with required fields `action`, `expected_revision`, `slice_agreement_ref` and `input_snapshot`; legacy `accept_slice_outcome` remains unchanged. The agent cannot synthesize a reopening or a human coverage decision. Stale evidence blocks closure, and all historical criteria remain applicable by default.
