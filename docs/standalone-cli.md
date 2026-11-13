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

The lifecycle surface contains `init`, `new`, `change`, `clarify`, `continue`, `status`, and `review`, plus a narrowly scoped E1 `accept-slice` user-confirmation command. The separate `ui` dashboard and `doctor` diagnostic are read-only local commands.

### `accept-slice` (E1 first Slice, explicit human consent)

The repository must be initialized and have no existing work. Read the current revision with `evospec status` before issuing the command, then enter the exact terminal confirmation shown. This is a **human terminal action**: headless/agent execution is intentionally refused. The command writes one Feature, one Slice, its first Task and pending checkpoint into the same authoritative State revision; it never starts implementation automatically.

```text
cd <repository>
evospec init
evospec status
evospec accept-slice --feature "Task manager" --slice "Local task list" --task "Implement task list" --checkpoint "Local list verified" --expected-revision 1
```

After acceptance, restart OpenCode V2 or run `evospec status` in another local session. The OpenCode `evospec_context` tool recovers the same immutable agreement references, Task ID and initial Task through Core's read-only context projection. State lives only in the selected repository's `.evospec` directory; copying the state to another root is rejected and cross-computer synchronization is out of scope. Older state schemas remain readable but cannot enter this first-Slice writer automatically. This path is not yet a native OpenCode approval-dialog implementation and does not prove OpenCode host permission parity.

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

No native archives or installer release assets are published from this source snapshot. The checked-in installers resolve assets from this repository and must only be used after an authorized release publishes matching files. This repository is private; no anonymous release bootstrap is available.

## Related Boundaries

Semantic Locks are Core-owned workflow state, not standalone CLI commands. See [semantic-locks.md](semantic-locks.md). The complete system boundary and persistence model are described in [architecture.md](architecture.md).
