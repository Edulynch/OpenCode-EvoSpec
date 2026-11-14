---
description: Show the current EvoSpec protocol context without mutation.
---
<!-- EvoSpec-managed: opencode-v2 -->
Load the `evospec` skill and call only `evospec_context` in the current session project. Answer as a short, readable recovery index: Feature, Slice, Task, last checkpoint, and what is known to remain. Distinguish the accepted initial checkpoint from optional later `task_progress` (`REPORTED_UNVERIFIED`); never invent missing criteria or completion. When no Task is active, list available accepted titles from `work_queue` as **inactive**, without choosing or activating a queued Task; explain multiple candidates or an incomplete index. If no trustworthy local state exists, explain that plainly. Show revision, IDs, legal actions, and advisory external SDD evidence only when requested or needed to explain a blocker. This is a read-only query: never initialize, continue, accept, repair, or change anything. User input: $ARGUMENTS
