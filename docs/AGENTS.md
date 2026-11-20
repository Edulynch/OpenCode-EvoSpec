# Documentation placement and evidence rules

This folder describes EvoSpec; it is not the runtime record store.

- Put architecture/boundary explanations in `architecture.md`, approved method-level behavior in `methodology.md`, agent wire protocol in `agent-protocol.md`, and OpenCode installation behavior in `opencode.md`.
- CLI behavior belongs in `standalone-cli.md`; lock invariants in `semantic-locks.md`; qualification and release evidence in their dedicated documents.
- Explicitly distinguish proposed methodology, implemented Core behavior, qualified native-host behavior and published releases. None implies the others.
- Document paths, commands, schema numbers and permission behavior from current code and verified evidence; never convert tests or aspirations into unqualified PASS claims.
- Link to the canonical code/record instead of copying mutable Feature/Slice/Task state into documentation.
- Keep new guides focused and update root README/ROADMAP only when the actual scope or milestones change.
