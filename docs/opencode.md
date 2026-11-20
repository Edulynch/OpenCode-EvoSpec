# OpenCode V2 integration

EvoSpec integrates with **OpenCode V2** through a dependency-free local plugin, nine native commands, and the `evospec` skill. The plugin registers typed tools that call EvoSpec's provider-neutral protocol or CLI for the current OpenCode session. It also adds a session-context hook that directs EvoSpec lifecycle work to the skill and canonical context.

> **E0 V2 boundary:** This page describes the current OpenCode integration and its nine `/evospec.*` commands. The unprefixed `/specify`, `/clarify`, `/plan`, `/implement`, `/status`, and `/change` names in [`methodology.md`](methodology.md) are approved E0 method-level UX, not commands provided by the current integration or a selected implementation design.

The integration writes only these EvoSpec-owned resources:

- `.opencode/plugins/evospec/index.ts`
- `.opencode/commands/evospec.*.md` (nine commands)
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

The local plugin is auto-loaded from `.opencode/plugins/evospec/index.ts`. It uses OpenCode V2's `ctx.tool.transform` API and the executing session's location to invoke `evospec-agent` and `evospec`. It registers the raw provider-neutral protocol tool and typed helpers for context, intake, progress, review, verification, continuation, clarification, discovery recovery, capability selection, work queue operations, and convergence.

For a human-accepted E1 Task, use the dedicated `evospec_accepted_task_progress` tool only when Core's current context offers that action. It requires the exact revision, repository ID, Task ID, accepted Feature/Slice references, checkpoint, statement and bounded evidence leads. OpenCode enforces its native tool permission; Core revalidates persisted scope and authorization. The generic `evospec` tool refuses the new action, and the legacy `evospec_progress` path cannot mutate an active accepted E1 Task. The dedicated action has no automatic stale-revision retry. Progress remains explicitly **unverified**, and no new slash command or E0 method command is created.

For a human-authorized schema-17 Slice, `evospec_criterion_test_r3b` records one fresh supported test per accepted criterion without accepting the Slice. Every criterion needs a TTY-approved, exact root-input contract bound to the full `r3-root-input-v2` repository fingerprint; a file or directory change stales coverage and evidence, and `renew-r3b-coverage` requires separate interactive approval before retesting. The test tool revalidates the original requester and current session root after execution and immediately before recording; mismatches submit no test-result request. `evospec_accept_slice_outcome` is a separate native-permission action that returns Core's immutable outcome receipt only when every current criterion remains fresh and passing. Stale revisions are not replayed. The V2 adapter installs an `evaluate` permission hook that forces an explicit `ask` for these two R3-B actions even when the host's normal rule would `allow`; without the hook, both actions fail closed. The permission request supplies the canonical root as a separate resource and in metadata, but **host receipt of fields does not prove they were displayed**. In OpenCode **v2.3.2**, the TUI generic renderer for custom tools shows the tool name but not the `repository_root` resource/metadata. Consequently, this client does **not** satisfy a stricter requirement to show the root in the popup; a silent or invisible approval is still a native qualification blocker. For the specifically owner-approved Windows T2/T3 qualification, **proportional oracle B** instead required a separate, read-only root/fingerprint preflight and the human's confirmation of the exact tool name and `Allow once` at each popup. This is a narrower documented test boundary, **not** evidence that the popup displayed the root or a universal permission guarantee. Test evidence is a bounded trusted-local declaration and operational signal, not observed-read closure, an OS sandbox, or proof that arbitrary same-user test code cannot read hidden host state. See [Agent protocol](agent-protocol.md#r3-b-separately-authorized-objective-slice-outcome) for the JSON format, CLI usage and limitations.

The `permission.asked` and `permission.replied` events in OpenCode v2.3.2 are **ephemeral**, not durable session records. EvoSpec test receipts and later diagnostic logs alone do not reconstruct every human `Allow once` decision. Record consent during the native session and distinguish the human's account from the model's tool output.

The V2 plugin has no runtime import from `@opencode/plugin`; OpenCode loads the local TypeScript module without project dependencies. At startup it verifies that all typed tools were registered. The skill is auto-discovered from `.opencode/skills/evospec/SKILL.md`. Commands remain ordinary V2 Markdown commands under `.opencode/commands/`.

The integration installs exactly these nine native commands:

1. `/evospec.init` — initialize EvoSpec authority for the repository.
2. `/evospec.start` — start a model-assessed new or changed work item.
3. `/evospec.continue` — recover the current work context when continuation is legal.
4. `/evospec.status` — show the current authoritative context without mutation.
5. `/evospec.clarify` — answer clarification questions returned by EvoSpec.
6. `/evospec.review` — record product review and, when legal, technical verification.
7. `/evospec.converge` — explicitly request convergence when it is legal.
8. `/evospec.context` — show protocol context and legal actions without mutation.
9. `/evospec.doctor` — run the local read-only diagnostic without auto-remediation.

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
