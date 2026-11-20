<div align="center">

# 🧬 EvoSpec

### Keep AI development aligned with what you actually meant.

**New session ≠ start from zero.**


EvoSpec keeps important decisions with the project so AI coding agents can continue without rebuilding context.
The model reasons. EvoSpec remembers and keeps the work aligned.

<p><a href="#-the-problem">Problem</a> · <a href="#quick-start">Quick Start</a> · <a href="#project-status">Status</a> · <a href="#-documentation">Docs</a></p>

</div>

## 💥 The Problem
AI can move your code quickly while the reason behind it slips out of view.
New sessions, handoffs, and parallel work can turn a clear goal into disconnected changes.

## 🧠 The Solution
EvoSpec gives OpenCode a durable, project-aware way to stay anchored to your intent.
Describe the outcome in plain language, then keep momentum without losing the decisions that matter.

```text
Your intent  →  Shared direction  →  Confident building  →  A result you recognize
```

## Quick Start

This repository currently provides a source-only installation. Git access and Python 3.11 or newer are required. No native installer/archive assets or PyPI release are published here yet.

1. **Clone and install EvoSpec from source.**

   ```sh
   git clone https://github.com/Edulynch/OpenCode-EvoSpec.git
   cd OpenCode-EvoSpec
   python -m pip install .
   ```

2. **Connect your project once.**

   ```text
   cd <your-project>
   evospec integrate opencode
   ```

3. **Initialize authority for a new project (only when it is not already initialized).**

   ```sh
   evospec init
   ```

4. **Open OpenCode and describe the work naturally.**
   > Add a dark mode to the dashboard. Keep it accessible and consistent with the current product.

   A request in plain language does **not** silently accept a Slice. New Feature/Slice agreements, criteria and test coverage require separate explicit human terminal decisions; see the [Standalone CLI guide](docs/standalone-cli.md).

5. **Optional: open the human dashboard.**

   ```text
   evospec ui
   ```

   The dashboard is local, loopback-only, read-only, and updates in realtime.

## 💬 Show Me EvoSpec
```text
You:      "Add a dark mode to the dashboard. Keep it accessible and consistent."
OpenCode: "I'll keep the work centered on that outcome as we investigate, build, and check it."
```

Start with the outcome; EvoSpec helps the conversation stay grounded. Human approval is still required for material agreements, protected test coverage and other gated decisions.

## ✨ What EvoSpec Helps With
- Keeping a product goal clear across long-running AI work.
- Giving handoffs a shared, understandable direction.
- Making changes easier to explain, review, and trust.
- Preserving project/work state across restarts.
- Showing current work, blockers, features, and progress in a human-readable dashboard.

## 🧬 How It Works
```text
Say what matters  →  Explore with context  →  Build with direction  →  Check the outcome
```

Core owns deterministic authority, lifecycle, recovery, and publication safety.
Agents provide attributable reasoning and use Core-owned operations instead of inventing project state.

Read the [EvoSpec Methodology](docs/methodology.md) for the deeper picture.

## Project Status

**Source/package version:** `0.5.0`. This public repository is **source-only**: a source version is not a published GitHub Release, native installer/archive or PyPI distribution. The supported installation path uses Python 3.11+ from a source checkout.

**Implemented on `master`:**

- **E0/E1:** owner-approved V2 methodology and durable, human-accepted Feature → Slice → Task/checkpoint, with read-only recovery across sessions ([E1 PR #3](https://github.com/Edulynch/OpenCode-EvoSpec/pull/3), [Task progress PR #6](https://github.com/Edulynch/OpenCode-EvoSpec/pull/6)). E0's proposed unprefixed UX commands are not necessarily installed commands.
- **R3-A/B:** deterministic criterion receipts and separately permissioned objective Slice outcome acceptance ([PR #14](https://github.com/Edulynch/OpenCode-EvoSpec/pull/14), [PR #16](https://github.com/Edulynch/OpenCode-EvoSpec/pull/16)).
- **R4-A/B:** read-only acceptance indexes for active and multiple Slices ([PR #19](https://github.com/Edulynch/OpenCode-EvoSpec/pull/19), [PR #21](https://github.com/Edulynch/OpenCode-EvoSpec/pull/21)).
- **R5-A/B/C:** a separately accepted next Slice, multi-Slice continuation and independent second-Slice test/outcome evidence ([PR #23](https://github.com/Edulynch/OpenCode-EvoSpec/pull/23), [PR #25](https://github.com/Edulynch/OpenCode-EvoSpec/pull/25), [PR #27](https://github.com/Edulynch/OpenCode-EvoSpec/pull/27)).
- **R6-A/B:** explicit human-confirmed Feature completion (schema 18) and human-authorized reopening cycles (schema 19), without implicitly promoting an accepted Slice into a finished Feature ([PR #29](https://github.com/Edulynch/OpenCode-EvoSpec/pull/29), [PR #31](https://github.com/Edulynch/OpenCode-EvoSpec/pull/31)).

**Verified separately from CI:** R3-B criterion and outcome testing through Windows OpenCode v2.3.2 ([T2 #36](https://github.com/Edulynch/OpenCode-EvoSpec/issues/36)); a second Slice with independent coverage, receipts and idempotent acceptance ([T3 #37](https://github.com/Edulynch/OpenCode-EvoSpec/issues/37)); and normal recovery across two independent, read-only OpenCode sessions ([T4 #38](https://github.com/Edulynch/OpenCode-EvoSpec/issues/38)). These specific native tests used an **explicitly owner-approved proportional permission boundary**: verify the root/fingerprint independently and confirm the exact custom-tool prompts and human `Allow once`. OpenCode v2.3.2's TUI does **not** render `repository_root` inside the generic custom-tool permission dialog; see [OpenCode integration](docs/opencode.md).

**Distinct gates:** Task progress `REPORTED_UNVERIFIED` is not a criterion `VERIFIED`; a Slice outcome `CURRENT` is not a terminal Slice or completed Feature. Windows **physical power-loss durability remains NOT TESTED**, cross-computer synchronization is out of scope, and the local evidence/permission model is not cryptographic same-user attestation or an OS sandbox. General native R6 end-to-end qualification and release-candidate installation/upgrade still require their own evidence. **T5 documentation and T6 release assessment** are tracked separately; no merge, tag or release is authorized by test results.

The historical **v0.6.0 Autonomy Pass** remains paused in [ROADMAP.md](ROADMAP.md). There is no published GitHub Release, PyPI distribution or native installer asset for this source snapshot.

## ⌨️ Optional Commands
Natural language is the default. Use a command only when you want a direct control point:

```text
/evospec.start
/evospec.status
/evospec.context
```

Useful CLI commands include:

```text
evospec status
evospec ui
```

See [OpenCode integration](docs/opencode.md) and the [Standalone CLI guide](docs/standalone-cli.md).

## Update

Update the source checkout and reinstall the package:

```sh
cd OpenCode-EvoSpec
git pull
python -m pip install .
```

Then refresh the integration in your project:

```text
cd <your-project>
evospec integrate --refresh
```

## 🗑️ Remove EvoSpec from a project
Remove EvoSpec's OpenCode integration from the project without disturbing unrelated setup:

```text
cd <your-project>
evospec integrate opencode --remove
```

For runtime uninstall details, see the [Standalone CLI guide](docs/standalone-cli.md).

## 🧩 Already using another SDD tool?
Bring EvoSpec into an existing specification-driven workflow without erasing its history.
Detected existing SDD context is preserved and never silently replaced.

## 📚 Documentation
- [Roadmap](ROADMAP.md)
- [OpenCode integration](docs/opencode.md)
- [EvoSpec Methodology](docs/methodology.md)
- [E0 V2 contract review and validation](docs/e0-v2-validation.md)
- [Standalone CLI](docs/standalone-cli.md)

## 🛠️ Development
For contributors working from a source checkout: Python 3.11+ is required.

```text
python -m pip install .
```

### Roadmap discipline

For this repository:

- every completed major task or feature updates `ROADMAP.md`;
- milestone changes also update the **Project Status** section above;
- validated major work should be integrated back to `master` so the default branch reflects current project truth.

## ❤️ License
EvoSpec is available under the [MIT License](LICENSE).
