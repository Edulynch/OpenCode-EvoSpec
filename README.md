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

3. **Open OpenCode and describe the work naturally.**
   > Add a dark mode to the dashboard. Keep it accessible and consistent with the current product.

4. **Optional: open the human dashboard.**

   ```text
   evospec ui
   ```

   The dashboard is local, loopback-only, read-only, and updates in realtime.

## 💬 Show Me EvoSpec
```text
You:      "Add a dark mode to the dashboard. Keep it accessible and consistent."
OpenCode: "I'll keep the work centered on that outcome as we investigate, build, and check it."
```

No ritual to memorize. Start with the outcome; EvoSpec helps the work stay connected to it.

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

**Source/package version:** `0.5.0` (source snapshot; not a published release in this repository).

**E0 V2 methodology:** Product-owner approved on 2026-11-12 as a method-level contract. **E1 Vertical Slice B is functionally complete and merged** ([PR #3](https://github.com/Edulynch/OpenCode-EvoSpec/pull/3), 2026-11-13): an explicitly accepted Feature/Slice/Task and checkpoint persist locally and are recovered in an independent OpenCode session. Windows native-host qualification used OpenCode 2.3.0; Linux/Windows CI passed. This does **not** close all E1 design gates, certify Windows power-loss durability, prove unforgeable human identity against same-user processes, or authorize a release. E1.2 specifies a portable application with repository/worktree-local state; cross-computer context synchronization is outside E1.

The **v0.6.0 — Autonomy Pass** is paused historical planning, not an active EvoSpec V2 objective. Its task state remains in [ROADMAP.md](ROADMAP.md).

CI validates this source snapshot; no native installer/archive assets, PyPI package, or GitHub Release have been published from this repository.

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
