# OpenCode V2 integration

EvoSpec integrates with **OpenCode V2** through a dependency-free local plugin, nineteen native commands, and the `evospec` skill. The plugin registers typed tools that call EvoSpec's provider-neutral protocol or CLI for the current OpenCode session. It also adds a session-context hook that directs EvoSpec lifecycle work to the skill and canonical context.

> **E0 V2 boundary:** This page describes the source integration and its nineteen `/evospec.*` commands. The unprefixed `/specify`, `/clarify`, `/plan`, `/implement`, `/status`, and `/change` names in [`methodology.md`](methodology.md) remain method-level UX; they are not installed by this integration.

The composed continuity MVP described in the [consolidated MVP contract](ac0-consolidated-mvp.md) is implemented in this source branch, but is not a merged release or independently qualified native OpenCode workflow. Automated adapter tests are not evidence that a live permission dialog appeared or that a user approved it.

The integration writes only these EvoSpec-owned resources:

- `.opencode/plugins/evospec/index.ts`
- `.opencode/commands/evospec.*.md` (nineteen commands)
- `.opencode/skills/evospec/SKILL.md`
- `.evospec/opencode-resources.json` (ownership and SHA-256 manifest)
- `.evospec/integrations.json` (the shared provider registry)

It does not create or edit `opencode.json`, `package.json`, npm lockfiles, instruction files, or lifecycle state. Existing OpenCode configuration, user resources, and foreign Spec-Kit material are preserved. The manifest is separate tool-owned integration state; it does not alter EvoSpec lifecycle authority.

## Requirements

Use OpenCode V2. This repository currently offers a source-only installation; Git access and Python 3.11 or newer are required. No native release bundle or installer assets are published here.

## Install EvoSpec from source

```sh
git clone https://github.com/Edulynch/OpenCode-EvoSpec.git
cd OpenCode-EvoSpec
python -m pip install .
```

The source package provides the `evospec` command in the Python environment used for installation. See [Standalone CLI](standalone-cli.md) for its lifecycle and native-distribution source details.

## Install

Run the integration in the repository that should receive the resources:

```text
cd <path>
evospec integrate opencode
```

From another directory, pass `--project <path>`:

```text
evospec --project <path> integrate opencode
```

The integration preflights every destination before writing and commits resources, manifest, and registration as one transaction. A second run reports `CURRENT` when the installed bytes and manifest match. Byte-identical packaged V2 resources can be claimed by the manifest without rewriting them; any other occupied destination is left untouched and reported as a conflict.

Preview without writing:

```text
cd <path>
evospec integrate opencode --dry-run
```

## OpenCode V2 resources

The local plugin is auto-loaded from `.opencode/plugins/evospec/index.ts`. It uses OpenCode V2's `ctx.tool.transform` API and the executing session's location to invoke `evospec-agent` and `evospec`. It registers the raw provider-neutral protocol tool and typed helpers for composed continuity, legacy context/intake/progress/review/verification/continuation, discovery recovery, capability selection, work queue operations, convergence, and the narrowly bounded request-scoped objective lane below.

For a human-accepted E1 Task, use the dedicated `evospec_accepted_task_progress` tool only when Core's current context offers that action. It requires the exact revision, repository ID, Task ID, accepted Feature/Slice references, checkpoint, statement and bounded evidence leads. OpenCode enforces its native tool permission; Core revalidates persisted scope and authorization. The generic `evospec` tool refuses the new action, and the legacy `evospec_progress` path cannot mutate an active accepted E1 Task. The dedicated action has no automatic stale-revision retry. Progress remains explicitly **unverified**, and no new slash command or E0 method command is created.

For a human-authorized schema-17 Slice, `evospec_criterion_test_r3b` records one fresh supported test per accepted criterion without accepting the Slice. Every criterion needs a TTY-approved, exact root-input contract bound to the full `r3-root-input-v2` repository fingerprint; a file or directory change stales coverage and evidence, and `renew-r3b-coverage` requires separate interactive approval before retesting. The test tool revalidates the original requester and current session root after execution and immediately before recording; mismatches submit no test-result request. `evospec_accept_slice_outcome` is a separate native-permission action that returns Core's immutable outcome receipt only when every current criterion remains fresh and passing. Stale revisions are not replayed. The V2 adapter installs an `evaluate` permission hook that forces an explicit `ask` for these two R3-B actions even when the host's normal rule would `allow`; without the hook, both actions fail closed. The permission request supplies the canonical root as a separate resource and in metadata, but **host receipt of fields does not prove they were displayed**. In OpenCode **v2.3.2**, the TUI generic renderer for custom tools shows the tool name but not the `repository_root` resource/metadata. Consequently, this client does **not** satisfy a stricter requirement to show the root in the popup; a silent or invisible approval is still a native qualification blocker. For the specifically owner-approved Windows T2/T3 qualification, **proportional oracle B** instead required a separate, read-only root/fingerprint preflight and the human's confirmation of the exact tool name and `Allow once` at each popup. This is a narrower documented test boundary, **not** evidence that the popup displayed the root or a universal permission guarantee. Test evidence is a bounded trusted-local declaration and operational signal, not observed-read closure, an OS sandbox, or proof that arbitrary same-user test code cannot read hidden host state. See [Agent protocol](agent-protocol.md#r3-b-separately-authorized-objective-slice-outcome) for the JSON format, CLI usage and limitations.

The `permission.asked` and `permission.replied` events in OpenCode v2.3.2 are **ephemeral**, not durable session records. EvoSpec test receipts and later diagnostic logs alone do not reconstruct every human `Allow once` decision. Record consent during the native session and distinguish the human's account from the model's tool output.

The V2 plugin has no runtime import from `@opencode/plugin`; OpenCode loads the local TypeScript module without project dependencies. At startup it verifies that all typed tools were registered. The skill is auto-discovered from `.opencode/skills/evospec/SKILL.md`. Commands remain ordinary V2 Markdown commands under `.opencode/commands/`.

The source integration packages twelve composed continuity commands:

1. `/evospec.specify` — create a Feature in a fresh root and explicitly opt into composed mode.
2. `/evospec.clarify` — record a decision against the current Feature revision.
3. `/evospec.plan` — record the current implementation plan.
4. `/evospec.tasks` — read Tasks or add an incrementally discovered Task.
5. `/evospec.implement` — query the queue, checkpoint before effects, and report afterward.
6. `/evospec.use` — inspect or select a Feature for this session or the worktree default.
7. `/evospec.change` — record a correction or amend a Feature, preserving closure history.
8. `/evospec.status` — show composed status read-only, with a legacy read-only fallback.
9. `/evospec.verify` — run one criterion's declared supported check and record its result.
10. `/evospec.pause` — persist a pause and optional related/external detour.
11. `/evospec.resume` — resume or explicitly reconcile an unresolved attempt.
12. `/evospec.close` — request closure only when current checks and Tasks satisfy Core guards.

Seven legacy commands remain: `/evospec.init`, `/evospec.start`, `/evospec.continue`, `/evospec.review`, `/evospec.converge`, `/evospec.context`, and `/evospec.doctor`. Composed and legacy authority are disjoint. `/evospec.status` and `/evospec.clarify` route only from Core's reported mode; they never migrate, initialize, or combine the stores. See the [composed MVP contract](ac0-consolidated-mvp.md) for schema, persistence, task/evidence distinctions, and limitations.

### R2-C2: detect host delegation contract candidates, fail closed

`evospec_auto_contract` is a separate, read-only **compatibility diagnostic** for the proposed Auto-first delegated authority in [#49](https://github.com/Edulynch/OpenCode-EvoSpec/issues/49). It reports whether the running host exposes a `permission.saved.list` function and whether an observed `permission.evaluate` event contains the candidate `rule.source` shape proposed in [OpenCode #61767](https://github.com/anomalyco/opencode/issues/61767). This observation is ephemeral and per-session: it stores a boolean, **not** rule values, resources, permission records, prompt texts, or user identities. It never calls the saved-rules function and does not issue a permission request.

Even if the new field or function is visible, its status is `SHAPE_OBSERVED_UNQUALIFIED` or `API_PRESENT_UNQUALIFIED`. Hook events can be altered by other plugins; a rule source alone does **not** establish that a human submitted a specific prompt, that the host is in Auto, or that policy did not change before execution. A trusted implementation also needs host-owned evidence of human-origin or explicitly configured delegation, exact root/session/action binding, policy revision/revocation and a **single-use execution admission** (see [OpenCode #61645](https://github.com/anomalyco/opencode/issues/61645)). All `required_trust` entries remain `UNVERIFIED`; every `automatic_*_allowed` flag remains `false`.

The stable output `AUTO_DELEGATION_CONTRACT_BLOCKED` is therefore a **dependency result**, not a failure of the user's Auto configuration. It does not modify EvoSpec Core state, host permissions, local files, acceptance receipts, or the active session. Normal application development is not expected to invoke this diagnostic. Do not represent an observed candidate API as a completed R2-C2 implementation; the host's future guarantees must be independently qualified before a new `HOST_DELEGATED` Core authority is considered.

### R2-C2A: session-request first Slice without terminal tokens (experimental)

For a **fresh, empty and initialized** E1 repository, `evospec_start(new)` now offers `evospec_session_first_slice(expected_revision)` as the default next tool for a small local single-file web app. The adapter reads the latest `type=user` admitted message preceding the invoking assistant tool call from `session.context`; the agent cannot submit a different message ID or rewrite the prompt. This is **session transport provenance**, not authenticated human identity: `session.prompt` can also create user-role messages. The tool calls `permission.assert` for `evospec_session_intake` and stops on any host denial; after a potentially asynchronous decision it rechecks the exact message, session root and Core revision.

**Multiline input:** normal prompts may contain paragraphs, bullet lists, tabs and CRLF or trailing line breaks. The adapter accepts those characters and passes the exact host text to Core, which retains its full statement/digest; only display titles trim surrounding whitespace. No request to “rewrite as one line” is part of the UX. Still reject NUL/unsupported control bytes, empty text, attachments or requests longer than the current 2048-character bounded intake contract.

Core records `source=SESSION_REQUEST`, a reference `session:<sessionID>:<messageID>:<text-sha16>`, Feature/Slice titles equal to the original request, and fixed generic Task/checkpoint. There is no invented user agreement on additional criteria. The initial autonomy envelope allows only a **new** `index.html` with eight bounded reversible operations, leaving the native host filesystem permission independent, preserving `deny`, retry budget zero, and no irreversibles. Existing files, unexpected root contents, stale state, other work or a missing trustworthy session context block. No terminal `ACEPTO` or Core `AUTHORIZE` for this exact first file; additional requested paths still require a separate authorization.

**Not yet delivered by C2A-start:** automatic fresh criterion testing, R3-B Slice outcome and Feature completion. A session-request agreement is *not* a certificate of success or a release decision. Do not use this path for high-risk or ambiguous work, do not auto-grant permissions on other paths, and do not imply that plugin-to-Core transport is a security boundary against arbitrary local code running as the same OS user. Real OpenCode dogfood and CI qualification remain distinct gates.

### Request-scoped objective: dedicated static-heading tool

The dedicated `evospec_request_scoped_objective` tool supports only the two exact original user-message forms and the inert one-`h1` `index.html` subset documented in [agent-protocol.md](agent-protocol.md#request-scoped-objective-one-exact-static-heading). It is not the general `evospec` raw protocol tool: that tool refuses this action, and the action is not a general lifecycle legal action. No new slash command is added.

The adapter recovers the original admitted `type: "user"` message from Core's canonical `source_reference` (`session:<sessionID>:<messageID>:<sha16>`) and full `request_sha256`, not by selecting the nearest user message. It requires one matching original and one unique actual invoking assistant ID in the exact executing session's bounded `session.context` transcript, with the source preceding invocation. Both before and after native permission it scans the entire returned transcript from beginning to end, checking plain records, valid unique IDs and supported event types. Any later user message must have no attachments and `text.trim()` exactly `continue` or `continúa`; substantive follow-ups, malformed/unknown records, duplicate IDs, or a missing/compacted original source fail closed. Post-permission it reloads Core and resolves the same source again, preserving the existing session/tool identity, root, repository, revision, stage and workspace-fingerprint checks.

Once the canonical user source and invoking assistant have been matched, every other top-level `type: "synthetic"` record anywhere in the returned bounded transcript—regardless of its position—is accepted only as an opaque non-user event. Its valid unique ID is checked, but its text, description, metadata, nested role/source/permission claims, or purported proof are never read or returned. It is not authenticated or trusted, and grants no consent, permission, authority, evidence, or provenance upgrade. OpenCode v2.3.2's public [synthetic API](https://github.com/anomalyco/opencode/blob/v2.3.2/packages/core/src/session/synthetic.ts) accepts caller-provided ID, text, description and metadata; the public [subagent completion producer](https://github.com/anomalyco/opencode/blob/v2.3.2/packages/core/src/session/subagent-completion.ts) and [Job.Info schema](https://github.com/anomalyco/opencode/blob/v2.3.2/packages/schema/src/job.ts) expose no protected correlation between that synthetic record and its actual producer notification/job. Its fields therefore cannot authenticate the event's origin. This is separate from the deliberate Core source binding, which resolves the original user message by canonical reference and full digest. Synthetic content may influence model choices, but not the persisted request, objective/scope, native-permission gate, or Core's fresh proof and verdict. The initial `SESSION_REQUEST` path remains unchanged and rejects synthetic events between the request and intake tool because no persisted source exists yet. Unknown/missing-source gates also remain unchanged. Do not infer provenance, fetch hidden history or bypass the dedicated host gate through raw Core/protocol or another local interface. A restart can recover only while the original remains visible in the same bounded transcript. Checks cover only the observed snapshot; there is no atomic guarantee for prompts arriving after the final host read. Host permission remains independent and proves neither human authorship nor native qualification.

Core independently verifies the actual file and performs its revision-bound evidence/outcome and terminal-closure writes. The exact success codes are `REQUEST_OBJECTIVE_VERIFIED`, then a fresh revalidated call returning `REQUEST_OBJECTIVE_COMPLETE`; exact completed replay returns `REQUEST_OBJECTIVE_ALREADY_COMPLETE`. A failed page is `BLOCKED` with `REQUEST_OBJECTIVE_CONTENT_NOT_VERIFIED`, without accepted criteria, outcome or closure.

This supported lane does not require a terminal `ACEPTO` token, but native tool permission is still separate from the original request and the Core objective check. Neither an allow effect nor model-authored/test-shaped output proves authorship, successful HTML, or completion. The admitted session message is transport provenance, not authenticated human identity; the local plugin and Core are not an OS sandbox against same-user code. Unsupported generic app requests, extra requirements, additional paths, or material choices must stop and use the existing workflow. These implementation contracts and automated tests do not claim independent OpenCode native qualification.

For this lane, the dedicated tool with native host permission is the only supported OpenCode mutation path. Do not fall back to direct Core, stdin-protocol, or Python calls if that tool is denied, canceled, or unavailable. Such local interfaces carry no OpenCode attestation; an effective `allow` does not establish Auto-mode origin or authenticated human identity, and this design provides neither protection from same-user local code nor atomic revocation. Core-only or harness checks do not qualify native OpenCode behavior.

### Request-scoped objective: personal-task web app

The same dedicated `evospec_request_scoped_objective(expected_revision)` tool supports Core's separately bounded `PERSONAL_TASKS_WEB` objective. It receives no model-authored operation, criterion, source text, result, or proof. The plugin uses the Core-projected ordered criteria and retains the original source, session/root, repository, revision, fingerprint, post-permission, and fresh revalidation checks. There is no additional permission assertion or TTY step; ordinary OpenCode file permission remains independent.

The adapter and skill permit only one `index.html` when Core reports the exact missing-artifact blockers and the current session-request scope, path, edit action, and operation budget still allow that file. The visible test affordances are `[data-evo-action="task-input"]`, `[data-evo-action="add-task"]`, one `[data-evo-task]` row per actual task, and—only for explicitly requested completion—`[data-evo-action="complete-task"]`. The baseline is create and consult; complete is in scope only when Core derives it from the original request as `EXPLICIT_REQUEST`. Design, layout, and persistence implementation details are discretionary. No extra files, features, dependencies, network activity, or material design questions are part of this lane. For read-only, material, unsupported, stale, or out-of-scope cases, do not write or suggest terminal recovery; report the exact Core blocker and safe ordinary-workflow guidance.

Core, not the plugin or model, runs the real browser proof. Platform availability, Windows-only setup, the Linux/other-platform `REQUEST_OBJECTIVE_BROWSER_UNAVAILABLE` path, and CI requirements are documented in the [Agent Protocol](agent-protocol.md#request-scoped-objective-personal-task-web-app). A successful `REQUEST_OBJECTIVE_VERIFIED` is followed by a distinct fresh-revision call for `REQUEST_OBJECTIVE_COMPLETE`. This is not independent live OpenCode native qualification.

### Auto-first R2-C1: read-only capability qualification (not yet automated acceptance)

OpenCode supports `session.permissions: "autoaccept"` in its TUI configuration, but that **does not provide** a trusted per-tool attestation of the user's chosen mode to plugin code. The permission `evaluate` hook exposes the effective `allow` or `ask` **effect**, not whether it came from Auto, a configured rule, or a previously saved approval. A `prompt` admission hook receives prompts before durable admission, including programmatic `session.prompt` calls, so an observed prompt is **not** proof of human origin. See the [OpenCode CLI settings](https://opencode.ai/docs/cli/config) and [plugin V2 server API](https://opencode.ai/docs/build/plugins/server).

The read-only `evospec_auto_readiness` tool reports the **executing session's root** (from `session.get`), which observation hooks are available, and whether a prompt or permission-rule effect was seen during the current plugin process. The observation is session-scoped, bounded to 64 sessions, contains **no prompt text, attachment contents, permission resources, or secrets**, and is not persisted. Unsupported prompt hooks are reported as unavailable rather than failing plugin startup. A changed/restarted host loses this observation and cannot silently inherit it.

Its explicit outcome is `AUTO_HOST_ATTESTATION_UNAVAILABLE` with `effective_auto_mode=UNVERIFIED`, `prompt_human_origin=UNVERIFIED` and **all automatic acceptance/grant/completion flags false**, even after an `allow` effect is observed. It never calls `permission.assert`, `session.update`, `permission.reply` or any Core writer. This is **R2-C1 only**: no TTY gate, Core validation, scoped edit requirement, or human decision rule has been loosened.

**Qualification needed for R2-C2:** prove a trustworthy host-issued, root/session/message/revision-bound delegation capability distinct from generic `allow` and programmatic prompt admission. If the host API cannot deliver that guarantee, present `BLOCKED` and an actionable integration dependency rather than inventing a `UserAcceptance(source="INTERACTIVE_TERMINAL")`.

### Accepted Slice: request one file edit without a blind permission loop (R2A-003)

After the human accepts the first Slice, Core deliberately starts with no file paths and without the reversible `edit` operation. A permitted Slice is not a file-write grant. The OpenCode plugin now exposes `evospec_propose_file_edit(expected_revision, path, remaining_operations?)` for an active, unambiguous **accepted** E1 Slice. It first queries Core's current `autonomy_evaluate` for `edit` on that exact repository-relative path. When permission is missing, it preserves existing permissions and stop conditions and proposes only the additional path, reversible `edit`, and a **finite operation budget** (three remaining Core operations by default; caller may request one to four). It never applies the grant itself. The strict provider-neutral protocol requires **all seven delta keys**, using `null` for unchanged authority fields; the helper supplies them automatically. Omitting a key yields `INVALID_REQUEST` before Core validation, while a complete delta with a zero operation budget is rejected by Core with its specific finite-budget reason. Core issues an explicit pending proposal whose **entire** delta requires a separate `AUTHORIZE`, `REJECT`, or `NARROW` user decision.

Fail closed for stale revisions, missing accepted Slice, unresolved human gates, existing proposals, malformed paths and incomplete canonical context; never retry or expand silently. This is a Core **authorization proposal**, not an OS filesystem sandbox or proof that the host gave write access. The actual OpenCode file permission remains independent. The base lifecycle/Task gate and E1 progress protocol are unchanged.

Core proposal rejection messages now preserve the safe, explicit `ServiceError` reason (for example, `an autonomy grant requires a finite positive operation budget`) through `WorkflowService` instead of emitting only a generic refusal. This improves agent remediation without granting permissions automatically.

### New Feature: first-Slice handoff (D-004)

When a **new material Feature** arrives in an initialized repository whose current projection shows an empty work slot and no accepted Slice, `evospec_start(operation="new")` now returns `FIRST_SLICE_HANDOFF_REQUIRED` instead of creating a classic active Feature/Change. The agent proposes only one user-observable Slice, an initial Task, and a checkpoint; `evospec_first_slice_handoff` checks the current Core revision and bounded empty-slot projection and returns an exact, read-only terminal command for `accept-slice`. Before the first agreement, `e1_schema_ready` can be `null`; **the proposal does not prove writer-schema readiness or full ledger emptiness**. The interactive Core operation makes those authoritative checks. No Feature, Change or agreement is created by this proposal.

The human runs the command **in an interactive terminal**, reviews the exact Core-rendered agreement, and enters `ACEPTO <token>` there to approve. A chat answer, model-issued question, or generic OpenCode permission dialog is **not** the Core-required attributable TTY acceptance. Once the human returns, the agent reloads `evospec_context` and continues only if the Feature, Slice and Task are actually accepted. Rejecting or ignoring the prompt creates no work; a stale revision or nonempty ledger fails closed. A previously active classic Feature cannot be converted by inventing an accepted Slice.

Legacy `evospec_start(operation="new", intake_route="legacy")` remains explicitly accessible for compatibility; `change` is unchanged. The new handoff is deliberately limited: it does not attach per-criterion mini-spec tests, certify Slice completion, authorize file writes or grant Git permissions. **Current UX limitation:** this is a single-command interactive TTY handoff, **not** zero-command native approval. A more ergonomic, provably attributable host-native confirmation remains an open investigation in [Dogfood R1 #43](https://github.com/Edulynch/OpenCode-EvoSpec/issues/43). Avoid silently treating an initial human intent as approval of unmentioned scope.

Natural language remains first-class. OpenCode should read canonical context first, respect the workflow's current legal actions and revisions, preserve foreign SDD evidence, and stop before convergence unless explicitly requested. **`legal_actions` and `continuation.next_action` describe lifecycle/work-queue guidance, not a global veto on specialized R3-B test/outcome tools**, which enforce their own permission, scope, coverage and Core guards. Do not bypass a refusal from those specialized tools. A new request in plain language does not constitute a persisted human-accepted Slice; a `CURRENT` objective outcome does not by itself complete the Task, Slice lifecycle or Feature.

Normal read-only recovery was qualified across two **independent** OpenCode sessions on Windows with the same Core revision and workspace fingerprint ([T4 #38](https://github.com/Edulynch/OpenCode-EvoSpec/issues/38)). This does **not** certify physical Windows power-loss recovery. The tools do not change EvoSpec's provider-neutral protocol.

## Refresh after an EvoSpec upgrade

Refresh every provider previously registered for the repository:

```text
cd <path>
evospec integrate --refresh
```

The V2 manifest records exact resource paths and hashes. Refresh updates only bytes that still match the last recorded ownership hash, restores missing resources named in the manifest, and stops on unknown edits. It never replaces, migrates, or deletes an unmanifested V1 installation automatically.

## Existing V1 integration

V1 plugins, instruction files, and V2-overlapping commands or skills are not adopted as V2 resources. If the V2 manifest is absent, exact byte-for-byte V2 resources may be claimed without changing their contents; legacy EvoSpec markers and any other occupied resource destinations cause a conflict. Review and remove or relocate old EvoSpec V1 resources yourself only when that is appropriate, then run the V2 install. Unrelated OpenCode and Spec-Kit files remain untouched.

OpenCode V2 does not load configured `instructions` entries. EvoSpec does not edit old `opencode.json` instruction references; any old entries remain user-owned configuration.

## Remove the integration

```text
cd <path>
evospec integrate opencode --remove
```

Removal deletes only resources named by a valid `.evospec/opencode-resources.json` manifest whose installed bytes still match the recorded hashes. It removes the V2 manifest and unregisters OpenCode transactionally. Modified, unsafe, or unmanifested resources are preserved and reported as conflicts. Removal does not uninstall EvoSpec itself.

If you have a separately installed native bundle, its owning installer handles native uninstall. No native release bundle is currently published from this repository. Do not use `integrate opencode --remove` for that purpose.

## Doctor and recovery

`evospec doctor` is read-only. It reports the manifest, registration, and resource states. `CURRENT` means the V2 resources match the manifest. `MISSING` or `STALE` means an owned V2 resource needs refresh. `CONFLICT` or `UNREADABLE` means manual review is required; doctor does not repair files or choose a recovery action.

The lifecycle protocol has a separate `/evospec.continue` command. Use it after reading `/evospec.context`, `/evospec.status`, or `/evospec.doctor` when returned guidance says continuation is legal. Treat terminal `NO_ACTION` and `USER` intervention as returned outcomes. Do not edit `.evospec` directly or invent a recovery step.

If context reports `initialization_state` as `UNINITIALIZED` or returns `INIT_REQUIRED`, run `/evospec.init` first. Integration registration is separate from lifecycle authority, so an integrated project can still require initialization.

## Troubleshooting

### A command or skill is missing

Confirm OpenCode V2 is running in the integrated project and inspect `evospec doctor`. After installation or refresh, restart OpenCode if its current session has not reloaded the local plugin and resource directories.

### Integration reports a conflict

Run with `--dry-run` to see the conflicting resource paths. The integration will not overwrite a user edit, an unmanifested file, or an old V1 installation. Preserve any needed content, resolve the collision explicitly, then retry.

### R6-B historical evidence in OpenCode

After a human explicitly reopens a Feature with the standalone `reopen-feature` CLI, `evospec_context` displays the current cycle and immutable history. The existing permission-gated `evospec_criterion_test_r3b` tool can validate a precisely named archived accepted Slice once a human has renewed its root-input coverage through `renew-r3b-coverage --slice-agreement-ref`. `evospec_accept_slice_outcome` accepts that optional historical reference and Core rechecks the archive, revision and fresh evidence. Neither tool activates a historical Task; the plugin never reopens the Feature or runs tests automatically.

### Nothing changes

`CURRENT` means the V2 resource bytes and ownership manifest already match. With `--remove`, no changes means no manifest-owned V2 integration is installed.

### I want to remove only EvoSpec files

Run `integrate opencode --remove` from the project directory or pass it with `--project`. Removal leaves unrelated OpenCode resources, configuration, and Spec-Kit material untouched. Unmanifested V1 resources are never deleted automatically.
