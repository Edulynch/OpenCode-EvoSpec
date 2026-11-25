---
description: Inspect or select the Feature for this session or worktree.
---
<!-- EvoSpec-managed: opencode-v2 -->
Call `evospec_continuity` with `operation: "use"`. With no Feature argument this is read-only and reports the session selection or worktree default. With a Feature ID, select it for this host session; set `scope: "worktree"` only when explicitly requested to change the shared default. Selection does not start, accept, or authorize work. User input: $ARGUMENTS
