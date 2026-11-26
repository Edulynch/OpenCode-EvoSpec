---
description: Request explicit closure of a fully checked composed Feature.
---
<!-- EvoSpec-managed: opencode-v2 -->
Before calling `evospec_continuity` with `operation: "close"`, inspect the current Feature, all Tasks and every criterion's current check. Core blocks closure if any Task is incomplete, any criterion lacks a fresh passing check, work is paused, or a Task outcome is unknown. Closure is an explicit lifecycle write, distinct from a reported result or passing check. The host permission for this tool is independent and does not attest human identity, acceptance, review, or verification. User input: $ARGUMENTS
