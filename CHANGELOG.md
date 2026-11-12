# Changelog

This changelog records product source changes carried by the v0.5.0 package snapshot. Previous-repository tags, release pages/assets, CI run records, and commit references are not imported or claimed here. This repository has not published a release or PyPI package.


## 0.3.3 - Source history

- Report uninitialized lifecycle authority as the recoverable `INIT_REQUIRED` workflow result across status, protocol, and OpenCode context without treating integration registration as initialization.

## 0.3.2 - Source history

- Candidate source version adds `EvoSpec <resolved archive version> installed successfully.` after successful Windows installer cleanup.
- Updated onboarding and release-readiness documentation for the installer workflow.
- Added the Windows/Linux release-candidate workflow sources; this changelog does not claim a run or published assets.

## 0.3.1 - 2026-09-10

- Fixed the Windows one-line bootstrap to download a complete script, execute it in the invoking PowerShell 7 process, preserve output and failures, and remove its temporary file.
- Made the installer-owned `current` directory take precedence in process PATH immediately, with exact transactional rollback and owned-entry removal on uninstall.

## 0.3.0 - 2026-09-09

- Replaced `--root` with `--project`, removing the public `--root` option as a pre-1.0 compatibility change.
- Added integration registration, provider-less refresh, installer sources, and product-first onboarding.
- Improved cross-platform release and installed-runtime reliability.

## 0.2.0 - 2026-09-09

- Added standalone-native packaging support for Windows x86_64, Linux x86_64, macOS arm64, and macOS x86_64; no native assets are published by this source-only repository.
- Added the macOS-compatible `shasum -a 256` installer checksum fallback when GNU `sha256sum` is unavailable.
- No PyPI publication is claimed for this source snapshot; wheel/sdist packaging remains available through the release build tooling.

## 0.1.0 - 2026-09-07

- First pre-1.0 baseline with the Agile SDD workflow, Semantic Locks, provider-neutral protocol, OpenCode native integration, and External SDD awareness.
- Hardened source installation and recovery paths and included the MIT license.
