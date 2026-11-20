# Native packaging ownership

This directory defines the standalone native bundle, its entrypoints and packaging metadata.

- Keep executable entrypoints and packaging specification here; put installer logic and release validation scripts in `scripts/`.
- Preserve CLI/protocol parity with source runtime, version identity, reproducible asset contents and clear platform boundaries.
- Do not add runtime Feature agreements, decisions, tests or publication records to the bundle's source tree.
- Packaging success alone is not native host qualification or authorization to publish a release.
- Exercise changes against isolated native smoke and release identity tests before claiming an install/upgrade path works.
