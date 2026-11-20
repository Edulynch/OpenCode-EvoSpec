# GitHub automation and repository governance

This directory owns repository CI and GitHub-side automation, not EvoSpec's persisted Feature/Slice/Task records.

- Keep workflow changes in their existing CI files and related repository governance files in their respective subdirectories.
- Preserve Linux/Windows coverage and failure-closed checks. A successful workflow does not prove a manual native OpenCode permission prompt or a publish decision.
- Record actual workflow evidence and reference exact runs; do not fabricate PASS outcomes, create release tags or publish artifacts without authorization.
- Do not store tokens, secret values or user-owned machine state in workflows or logs.
- Keep release assets/status declarations consistent with the source-only/publication boundary in README and release-readiness documentation.
