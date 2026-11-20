# Changelog

This changelog records product source changes carried by the v0.5.0 package snapshot. Previous-repository tags, release pages/assets, CI run records, and commit references are not imported or claimed here. This repository has not published a release or PyPI package. Its GitHub repository is public but the supported distribution is currently source-only.

## Unreleased — source-only 0.5.0 evolution (November 2026)

- **E0/E1:** owner-approved V2 methodology, durable Feature/Slice/Task/checkpoint agreements and recovery across sessions ([PR #3](https://github.com/Edulynch/OpenCode-EvoSpec/pull/3)); guarded accepted-Task progress ([PR #6](https://github.com/Edulynch/OpenCode-EvoSpec/pull/6)).
- **R3:** objective criterion receipts and separate, idempotent Slice outcome acceptance under explicit known-input coverage ([PR #14](https://github.com/Edulynch/OpenCode-EvoSpec/pull/14), [PR #16](https://github.com/Edulynch/OpenCode-EvoSpec/pull/16)); Windows executable fingerprint and mandatory native permission corrections ([PR #33](https://github.com/Edulynch/OpenCode-EvoSpec/pull/33), [PR #34](https://github.com/Edulynch/OpenCode-EvoSpec/pull/34)).
- **R4/R5:** active/multi-Slice acceptance indexes, independently approved subsequent Slices, read-only continuation and separate evidence/outcomes for a second Slice ([PR #19](https://github.com/Edulynch/OpenCode-EvoSpec/pull/19), [PR #21](https://github.com/Edulynch/OpenCode-EvoSpec/pull/21), [PR #23](https://github.com/Edulynch/OpenCode-EvoSpec/pull/23), [PR #25](https://github.com/Edulynch/OpenCode-EvoSpec/pull/25), [PR #27](https://github.com/Edulynch/OpenCode-EvoSpec/pull/27)).
- **R6:** independent human-confirmed Feature completion (schema 18) and explicit reopening cycles (schema 19) ([PR #29](https://github.com/Edulynch/OpenCode-EvoSpec/pull/29), [PR #31](https://github.com/Edulynch/OpenCode-EvoSpec/pull/31)).
- **Qualification evidence (not a release):** Windows OpenCode v2.3.2 R3-B and R5-C native tests passed under an explicitly owner-approved proportional permission oracle ([T2 #36](https://github.com/Edulynch/OpenCode-EvoSpec/issues/36), [T3 #37](https://github.com/Edulynch/OpenCode-EvoSpec/issues/37)). Normal read-only recovery across two independent native sessions passed ([T4 #38](https://github.com/Edulynch/OpenCode-EvoSpec/issues/38)). Windows power-loss durability remains untested.
- These merged changes and tests do **not** publish native assets or a GitHub/PyPI release. Release-candidate assessment remains a separate decision ([T6 #40](https://github.com/Edulynch/OpenCode-EvoSpec/issues/40)).

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
