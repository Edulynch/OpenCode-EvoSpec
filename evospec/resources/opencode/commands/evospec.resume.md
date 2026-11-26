---
description: Resume or explicitly reconcile interrupted composed work.
---
<!-- EvoSpec-managed: opencode-v2 -->
Read continuity status first. If it reports a pending document transaction, use the journaled `resume` operation; it completes only writes whose bytes still match the old or intended digest and stops on manual divergence. Otherwise, resume a paused Feature. If a Task outcome is unknown, name the exact checkpointed Task, explicitly reconcile `COMPLETE` or `INCOMPLETE`, and cite the evidence reference; never retry it blindly. Include a detour-return summary so external changes remain visible, without overwriting them. User input: $ARGUMENTS
