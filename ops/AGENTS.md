# Operational infrastructure ownership

This directory is for operational tooling (including runner support), not feature specifications or persisted workflow truth.

- Keep operational configuration and scripts scoped to the relevant infrastructure component.
- Do not store credentials, tokens, private machine paths, user-level runtime state or production evidence in tracked files.
- Prefer reproducible, read-only diagnostics and isolated validation before changing operational behavior.
- Do not silently reconfigure runners, machines, hosting, secrets, remote branches or release automation.
- Distinguish CI automation status from Core verification, OpenCode native qualification and release approval.
