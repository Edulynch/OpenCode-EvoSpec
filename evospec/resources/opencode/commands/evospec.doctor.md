---
description: Run the local EvoSpec diagnostic without changing state.
---
<!-- EvoSpec-managed: opencode-v2 -->
Load the detailed `evospec` skill, then run the local `evospec doctor` diagnostic and report its complete result. Doctor is read-only and local. It never changes lifecycle state, repairs files, initializes authority, integrates resources, or chooses a lifecycle action automatically. If recovery is needed, read the returned guidance and context, then follow only the currently legal action. User input: $ARGUMENTS
