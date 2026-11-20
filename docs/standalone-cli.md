# Standalone CLI

The standalone CLI is a thin local adapter around EvoSpec's workflow service. It reads and writes the selected repository's `.evospec` state. It does not replace the Core workflow or publish Product Truth by itself.

> **E0 V2 boundary:** This guide describes the existing standalone CLI. The E0-approved command table in [`methodology.md`](methodology.md) is a method-level UX contract, not a CLI/API implementation or a decision to change the current command surface.

For the workflow model, see [methodology.md](methodology.md). For the storage boundary, see [architecture.md](architecture.md).

## Install from source

The current installation path is a source checkout and requires Git access plus Python 3.11 or newer. This repository has no published native archives, installer assets, or PyPI release.

```sh
git clone https://github.com/Edulynch/OpenCode-EvoSpec.git
cd OpenCode-EvoSpec
python -m pip install .
```

The installed `evospec` command runs from the Python environment used for installation. Use the project directory for lifecycle commands:

```text
cd <repository>
evospec --version
evospec status
```

The checked-in native installer and bundle sources are for a future authorized release. Do not download or run them as release installers until matching assets are actually published.

### `doctor`

Run the local diagnostic without changing repository state.

```text
cd <repository>
evospec doctor
```

`doctor` is read-only and local. It does not repair files, initialize authority, integrate resources, or select a lifecycle action automatically. If it reports a recovery need, read the returned guidance and run the specifically supported command after checking `evospec status` or `evospec context`.

### `ui`

Start the embedded project dashboard:

```text
cd <repository>
evospec ui
```

The dashboard is a local, offline, read-only view of EvoSpec's canonical project context. It binds only to `127.0.0.1` on port `8765` by default and has no lifecycle or write endpoints. It opens a browser after the server binds successfully. Select another valid TCP port or suppress browser launch when needed:

```text
evospec --project <repository> ui --port 9000 --no-open
```

From another directory, select the repository explicitly:

```text
evospec --project <repository> status
```

### Direct source invocation

For an optional source checkout workflow without installing the package, run the module with Python 3.11 or newer:

```text
cd <repository>
python -m evospec.cli status
```

The package installation above is the normal source-based path and also provides the `evospec` console entry point.

## Lifecycle Commands

The lifecycle surface contains `init`, `new`, `change`, `clarify`, `continue`, `status`, and `review`, plus narrowly scoped E1 `accept-slice`, `accept-next-slice`, `revise-slice-criteria`, R6-A `complete-feature`, and R6-B `reopen-feature` terminal-confirmed commands. The separate `ui` dashboard and `doctor` diagnostic are read-only local commands.

### `accept-slice` (E1 first Slice, explicit human consent)

The repository must be initialized and have no existing work. Read the current revision with `evospec status` before issuing the command, then enter the exact terminal confirmation shown. This is a **human terminal action**: headless/agent execution is intentionally refused. The command writes one Feature, one Slice, its first Task and pending checkpoint into the same authoritative State revision; it never starts implementation automatically.

```text
cd <repository>
evospec init
evospec status
evospec accept-slice --feature "Task manager" --slice "Local task list" --task "Implement task list" --checkpoint "Local list verified" --expected-revision 1
```

After acceptance, restart OpenCode V2 or run `evospec status` in another local session. The OpenCode `evospec_context` tool recovers the same immutable agreement references, Task ID and initial Task through Core's read-only context projection. State lives only in the selected repository's `.evospec` directory; copying the state to another root is rejected and cross-computer synchronization is out of scope. Older state schemas remain readable but cannot enter this first-Slice writer automatically. This TTY acceptance path is not a native OpenCode approval-dialog implementation and does not prove OpenCode host permission parity. Normal read-only recovery was subsequently verified across two independent OpenCode v2.3.2 sessions ([T4 #38](https://github.com/Edulynch/OpenCode-EvoSpec/issues/38)); **physical Windows power-loss recovery remains untested**.

### `accept-slice --criteria-file` (R1-D1 criteria in the **same** acceptance)

Prepare a JSON mini-spec for **only the next useful Slice**. Each stable criterion identifies observable behavior and the evidence expected later; describing evidence here does not assert that tests ran or passed.

```json
{
  "outcome": "Users see their locally saved tasks after reopening the app",
  "example": "A saved task appears again when the app restarts",
  "inclusions": ["Show existing tasks", "Persist local tasks"],
  "exclusions": ["No account system", "No cross-device sync"],
  "decisions": ["Use local storage for this Slice"],
  "criteria": [
    {
      "id": "AC-1",
      "expected_result": "A saved task reappears after restart",
      "expected_evidence": "Local restart test"
    }
  ]
}
```

Save the object as `slice-criteria.json`. From a PowerShell terminal in the target repository:

```powershell
cd 'D:\WORKSPACE\Personal\Tools\YourConsumerProject'
git branch --show-current
evospec status
evospec accept-slice --feature 'Task manager' --slice 'Local task list' --task 'Implement task list' --checkpoint 'Local list verified' --criteria-file '.\slice-criteria.json' --expected-revision 1
```

The CLI displays the entire canonical agreement and prompts for one `ACEPTO <token>` confirmation. Core publishes the Feature, Slice, initial Task, pending checkpoint, and immutable criteria revision under one State CAS. The model/agent cannot silently accept on the user's behalf.

### `accept-next-slice` (R5-A, existing Feature, sequential)

After the first Slice, EvoSpec can accept one more Slice **under the same Feature identity**, provided the active work slot is empty. Complete or explicitly park the previous work using its lifecycle operation first; the command does **not** auto-park, finish, or accept the previous outcome. This writer requires a previously human-approved schema-17 authority; earlier schemas do not migrate automatically.

Read the current State revision with `evospec status` and the original Feature-ID from the read-only `evospec_context` / acceptance inventory (status also displays the Feature-ID while work is active). Prepare a complete criteria file in the JSON format above. In a real interactive PowerShell terminal:

```powershell
cd 'D:\WORKSPACE\Personal\Tools\YourConsumerProject'
git status --short
git branch --show-current
evospec status
evospec accept-next-slice --feature-id 'feature-EXACT-ID' --slice 'Create local task' --task 'Implement local creation' --checkpoint 'New task survives restart' --criteria-file '.\slice-criteria.json' --expected-revision 5
```

All identities, the revision and the criteria path above are examples, not defaults. The CLI displays the existing Feature and **new Slice agreement**, then requires a fresh `ACEPTO <token>`. Core records the new Change, paired agreements, first Task/checkpoint and criteria head in one authoritative CAS revision, preserving earlier records and parked work. The R4-B index shows both Slices, but agreement **does not mean the new outcome was verified or accepted**. New R3-B criterion coverage needs its own separately approved authority; it is never inherited from the first Slice. No agent protocol endpoint synthesizes human confirmation. The second Slice needs its **own** approved known-input coverage and test receipts; a `CURRENT` objective outcome is separate from reported Task progress and terminal lifecycle completion. Earlier Slices can remain `PARKED` with immutable historical receipts whose evidence is not necessarily fresh under a changed whole-root fingerprint.

### `complete-feature` (R6-A, independent Feature closure)

The explicit Feature completion gate requires **every canonical Slice agreement** under the chosen Feature to have its own **ACCEPTED terminal lifecycle archive**, a currently valid criteria head, complete fresh R3-B evidence, and an existing matching objective Slice outcome receipt. Pending/parked/active work, unresolved decisions and relevant drift block closure. A historical receipt alone is not proof of freshness. The Core evaluator reads inactive Slices directly without activating work, running tests, or renewing coverage. If earlier evidence became stale because the repository changed, requalify it through a separately authorized process before retrying.

After checking the Feature-ID, current revision and local status, use a **real interactive terminal**:

```powershell
cd 'D:\WORKSPACE\Personal\Tools\YourConsumerProject'
git status --short
git branch --show-current
evospec status
evospec complete-feature --feature-id 'feature-EXACT-ID' --condition 'All agreed Feature results are verified and delivered' --expected-revision 12
```

The values above are **examples**. The CLI prints the exact Feature identity, the condition you supply, all matching Feature agreements, every included Slice and outcome receipt, the root fingerprint, State revision, and first schema conversion (**17→18**). Enter the displayed `ACEPTO <token>` only after reviewing the whole statement. If the original Feature agreement does not express its own completion condition, this is a **new explicit human material decision**, stored together with its immutable completion receipt. Schema 17 stays readable; the conversion to 18 happens only after this terminal decision and a fresh in-lock root scan. No project is migrated by a read-only status query.

Successful completion commits **one new State revision** and an immutable Feature receipt without changing the previous agreements, Slice outcome receipts, archives or Product Truth. An identical retry returns `ALREADY_COMPLETED` without a new write. A subsequent Slice request against that Feature fails with `REOPEN_DECISION_REQUIRED` until the separate R6-B human reopening gate is approved. `evospec status` and the OpenCode `evospec_context` tool expose the same read-only completion status, blockers and receipt identity. No model run, merge, tag, release or GitHub issue closure follows from this command.

### `reopen-feature` (R6-B, explicit new Feature cycle)

Only a **completed Feature** may be reopened, preserving its existing Feature-ID. Inspect `evospec status` for the current cycle, prior Feature completion receipt and State revision. In a real PowerShell terminal:

```powershell
cd 'D:\WORKSPACE\Personal\Tools\YourConsumerProject'
git branch --show-current
evospec status
evospec reopen-feature --feature-id 'feature-EXACT-ID' --previous-receipt-id 'feature-completion-EXACT-ID' --reason 'New requirement approved by the user' --purpose 'Continue with the next auditable Slice' --expected-revision 13
```

All identities and revisions above are **examples**, not defaults. The CLI displays the exact Feature, prior completion, reason, purpose, root fingerprint and prospective new cycle. Only the human may enter `ACEPTO <token>` to authorize one atomic schema 18→19 conversion and append one immutable `FeatureReopenReceipt`. Exact identical retries return `ALREADY_REOPENED` without advancing the revision. Incorrect roots, receipts, revisions, decisions or nonterminal contexts fail closed.

Reopening **does not create, accept, activate or resume a Slice**. The next Slice requires a separate `accept-next-slice` TTY agreement. Earlier completions remain valid history; a later `complete-feature` binds the new cycle and requires fresh evidence for **every applicable Slice**.

After a root change, old Slice coverage and tests become stale. An archived accepted Slice can obtain fresh human-approved coverage while its Feature cycle is open:

```powershell
evospec renew-r3b-coverage --slice-agreement-ref 'agreement-slice-EXACT-ID' --coverage-file '.\r3b-coverage.json' --expected-revision 14
```

The host may then execute the separately permitted `evospec_criterion_test_r3b` for that historical Slice and `evospec_accept_slice_outcome` with `slice_agreement_ref` to publish its current outcome. Core verifies the terminal archive, original agreement and fingerprint without activating the Task. No tests or models run automatically, and no old criterion is silently excluded. CLI and agent status display the same read-only cycle chronology; reads never migrate schema.

For a material amendment, first read `evospec status` or the read-only `evospec_context` result to obtain the **current** criteria revision. Write the replacement complete JSON mini-spec and use:

```powershell
cd 'D:\WORKSPACE\Personal\Tools\YourConsumerProject'
git branch --show-current
evospec revise-slice-criteria --criteria-file '.\slice-criteria.json' --previous-criteria-revision 'slice-criteria-EXAMPLE' --reason 'Approved material scope change' --expected-revision 2
```

This is a **separate `DECIDO <token>` decision**, not a repeat of the initial acceptance. The predecessor remains immutable, and both the State revision and criteria head are checked before committing. Substitute the actual values read from Core; example identities and revisions are not defaults. Previously accepted states without criteria report `NOT_RECORDED`; a deliberate decision may add their first revision with `--previous-criteria-revision NOT_RECORDED`. Lightweight work need not use the criteria command.

**Schema compatibility boundary:** Historical schema **13** remains supported with its existing behavior. An accepted E1 Task on schema 13 requires an explicit, revision- and source-bound human upgrade to schema **14**; this does not repeat Slice acceptance. Schema 14 preserves accepted Task progress, checkpoints, and the upgrade receipt, while criteria remain `NOT_RECORDED` until a separate explicit material criteria decision promotes the state to schema **15**. Schema 15 retains Task progress and immutable agreements alongside the canonical criteria history and head. R3-A can be enabled separately by `evospec upgrade-r3-schema --expected-revision <revision>`, requiring an interactive, exact state-digest-bound `ACEPTO` confirmation to schema **16**. This enables host-run deterministic tests per current criterion, without accepting the Slice or making a Product Truth/release decision. That schema-16 transition does not itself enable R3-B outcome acceptance, establish R4 acceptance-index results, or complete a Feature; these are distinct, separately gated capabilities.

### `init`

Create the initial EvoSpec state for a repository.

```text
cd <repository>
evospec init
```

### `new`

Record a new capability intent.

```text
cd <repository>
evospec new "<intent>"
```

### `change`

Record an intent to change an established capability.

```text
cd <repository>
evospec change "<intent>"
```

### `clarify`

Answer returned material clarification questions. Each answer uses the exact question identifier followed by `=` and the answer text.

```text
cd <repository>
evospec clarify <QUESTION_ID>=<answer>
```

Several answers can be supplied in one invocation:

```text
cd <repository>
evospec clarify <QUESTION_ID>=<answer> <QUESTION_ID>=<answer>
```

### `continue`

Reload persisted workflow state and continue the active work.

```text
cd <repository>
evospec continue
```

### `status`

Inspect the current workflow status without changing it.

```text
cd <repository>
evospec status
```

### `review`

Inspect the durable review state without recording review or verification evidence. Review recording, verification, convergence, and Semantic Lock operations are application API or agent protocol operations, not additional standalone CLI commands.

```text
cd <repository>
evospec review
```

The command prints a compact, pipe-separated result containing the outcome code, revision, feature and change identifiers, phase, and message. A workflow error is printed as `ERROR ...` and returns a nonzero process status.

## OpenCode Installation Utility

`integrate opencode` is separate from the lifecycle CLI. It installs the OpenCode V2 local plugin, nine native commands, the EvoSpec skill, and `.evospec/opencode-resources.json`. It does not edit `opencode.json`, create project Node dependencies, add a lifecycle subcommand, or perform automatic convergence.

Preview an installation:

```text
cd <repository>
evospec integrate opencode --dry-run
```

Apply the installation:

```text
cd <repository>
evospec integrate opencode
```

After an EvoSpec upgrade, refresh all providers that were set up for this repository:

```text
cd <repository>
evospec integrate --refresh
```

The provider registry and per-resource ownership manifest are separate tool-owned state under `.evospec`; they do not alter lifecycle state. Refresh dispatches registered providers and recognizes a valid V2 manifest. It does not adopt or migrate V1 integration resources or infer an integration from arbitrary `opencode.json` configuration.

Remove manifest-owned V2 resources whose bytes still match their recorded hashes:

```text
cd <repository>
evospec integrate opencode --remove
```

Removal unregisters OpenCode. `--refresh` is provider-less and cannot be combined with `opencode` or `--remove`.

The utility stops on conflicts instead of overwriting unrelated files. An exact byte match to a packaged V2 resource can be claimed without rewriting it; all other unmanifested files remain conflicts. Unmanifested V1 resources are preserved and are never migrated or deleted automatically. OpenCode V2's native command surface and the provider-neutral agent protocol are documented in [opencode.md](opencode.md) and [agent-protocol.md](agent-protocol.md).

## Native Distribution

Native bundles, when built and released, do not require Python at runtime. The current source-only snapshot has no native archive or installer release assets; its supported installation path requires Python 3.11 or newer. The source/build pipeline targets Windows x86_64 and Linux x86_64. macOS native distribution is deferred for a future release.

Native archive source/build layout for a future authorized release:

| Platform | Archive | Included entry points |
| --- | --- | --- |
| Windows x86_64 | `evospec-<version>-windows-x86_64.zip` | `evospec.exe`, `evospec-agent.exe`, `evospec-agent-protocol.exe` |
| Linux x86_64 | `evospec-<version>-linux-x86_64.zip` | `evospec`, `evospec-agent`, `evospec-agent-protocol` |

Each archive has exactly one top-level `evospec/` directory. The installer keeps versions under `%LOCALAPPDATA%\EvoSpec\versions\<version>` on Windows or `${XDG_DATA_HOME:-$HOME/.local/share}/evospec/versions/<version>` on POSIX systems. The `current` link or junction selects the active version. The installer owns only this root and the PATH entry it adds. It does not take ownership of an existing unmarked installation.

Installers verify the exact platform line in `SHA256SUMS`, reject unsafe or duplicate archive paths, stage the archive, and run `evospec --version` before activation. Activation moves the previous `current` target to a rollback location, then restores it if activation or the post-activation smoke check fails. Failed updates therefore leave the prior active version selected when rollback is possible.

The POSIX installer manages one marked line in the selected profile file, defaulting to `$HOME/.profile` or `$PROFILE` when supplied. The Windows installer manages the user PATH, or a caller-supplied path file for tests and controlled environments, and prepends its owned `current` directory to the invoking process PATH. Transactional failure restores the exact process PATH snapshot; uninstall removes the owned entry from both PATH scopes. Uninstall removes only an installer-owned native installation and its owned PATH entry. Native uninstall does not remove the Python package or project-local OpenCode resources.

No native archives or installer release assets are published from this source snapshot. The checked-in installers resolve assets from this repository and must only be used after an authorized release publishes matching files. This repository is public, but no published release assets exist for an installer bootstrap. Installing from checked-out source remains the supported path.

## Related Boundaries

Semantic Locks are Core-owned workflow state, not standalone CLI commands. See [semantic-locks.md](semantic-locks.md). The complete system boundary and persistence model are described in [architecture.md](architecture.md).
