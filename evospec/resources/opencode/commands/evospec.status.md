---
description: Show authoritative EvoSpec status without mutation.
---
<!-- EvoSpec-managed: opencode-v2 -->
Load the `evospec` skill and call only `evospec_context` for the current session project. Present a short human-readable status: Feature, Slice, Task, initial agreement, optional later Task report (`REPORTED_UNVERIFIED`), and any authoritative blocker or next permitted step. If no Task is active, show available accepted `work_queue` items as **inactive** and make valid alternatives explicit without selecting one. Explain absent, incomplete, or ambiguous context instead of inventing progress, completion, or criteria. Provide technical detail only on request. This is a local, read-only consultation; do not call continuation, initialize, repair, accept again, or execute work on this query. User input: $ARGUMENTS
