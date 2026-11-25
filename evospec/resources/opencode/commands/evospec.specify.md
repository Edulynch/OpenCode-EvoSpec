---
description: Opt a fresh or already-composed worktree into a document-backed Feature.
---
<!-- EvoSpec-managed: opencode-v2 -->
Use the `evospec` skill and the dedicated `evospec_continuity` tool. Read continuity status first. For a fresh repository, use `specify` with a Feature outcome, explicit criteria and allow-listed test targets, optional increments/Slices, an initial Task list and plan. In an already composed worktree, this creates another Feature. If legacy `.evospec/state.json` exists or mode is mixed/corrupt, stop: do not initialize, migrate, or switch modes. Resolve material scope questions before writing; a permission result is not Feature acceptance. User input: $ARGUMENTS
