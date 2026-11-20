# Scripts, installers and release tooling

This folder owns development scripts, build orchestration and platform installers.

- Keep install/upgrade scripts, release asset assembly and identity validation in their existing scripts; `packaging/` owns native bundle entrypoints and specifications.
- Never confuse a local build, source version or CI PASS with a published GitHub Release, PyPI package or verified native installer.
- Preserve path safety, checksum verification, owned-install isolation and rollback behavior across Windows and POSIX.
- Use isolated test roots and fake installation targets; avoid mutating global user PATH, real installations or credentials except under separately authorized testing.
- Require explicit release approval before creating a tag, publishing assets, changing release channels or treating builds as available for download.
