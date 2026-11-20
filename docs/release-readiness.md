# Release Readiness

## Current source snapshot

This public repository currently contains the EvoSpec `0.5.0` source/package version. That version is not a release published by this repository. Prior-repository Git history, tags, GitHub Releases, release assets, CI run evidence, and PyPI publication are not imported or claimed here. No native archive or installer assets are currently published here.

The supported installation path is a source checkout from this public repository, with Git access and Python 3.11 or newer:

```sh
git clone https://github.com/Edulynch/OpenCode-EvoSpec.git
cd OpenCode-EvoSpec
python -m pip install .
```

The checked-in native installer and build sources are retained for a future authorized release. They must not be presented as downloadable installers until matching assets have actually been published. No release validation or publication is claimed for this snapshot.

## Qualification is not a published release

Merged PRs implementing E1 and R3–R6, green source/adapter CI checks, and real OpenCode host tests establish **different** kinds of evidence. R3-B and second-Slice R5-C passed their separately owner-approved Windows native qualification, including proportional human permission evidence ([T2 #36](https://github.com/Edulynch/OpenCode-EvoSpec/issues/36), [T3 #37](https://github.com/Edulynch/OpenCode-EvoSpec/issues/37)). Normal context recovery passed across two independent read-only OpenCode sessions ([T4 #38](https://github.com/Edulynch/OpenCode-EvoSpec/issues/38)). **Physical Windows power-loss durability remains untested**, and general release-candidate install/upgrade smokes and separate native R6 end-to-end qualification have not been established.

Public GitHub visibility does **not** imply published native assets, a GitHub Release or PyPI package. The release-candidate gate is tracked independently in [T6 #40](https://github.com/Edulynch/OpenCode-EvoSpec/issues/40), and explicit `RELEASE_GO` remains required. No merge, tag or publication follows automatically from test results.

## Release Policy

Versions follow Semantic Versioning. Until `1.0.0`, incompatible changes can occur in minor releases; patch releases remain backwards-compatible fixes. A release tag must be an annotated `v<version>` tag whose version exactly matches `pyproject.toml`, created only after the release gates pass. The release identity validator mechanically checks the exact tag name, its annotated object type, and that it points at `HEAD`; it accepts PEP 440 prerelease strings by exact equality. The validator only reads repository state and never creates tags. This repository does not create tags or publish artifacts automatically.

Release gates are: clean source tests and type checking; wheel and sdist built once; both artifacts audited for project name/version and MIT license metadata, license payloads, package resources, and all console entry points; and a wheel rebuilt from the sdist and exercised outside the checkout using the smoke script's interpreter. The workflow sources retain Windows x86_64 and Linux x86_64 native build/release-candidate checks; macOS distribution remains deferred. Tag-triggered validation is read-only. Workflow definitions are not evidence that a run completed, and this snapshot claims no run or resulting assets. These gates validate the Python release; they do not publish it.

The inspected source tree currently contains **no `uv.lock`**. Do not claim lockfile-based reproducibility evidence that is not present. If dependency locking becomes a future development requirement, add and validate it with its own authorization; do not create or change dependency authority implicitly during release-only checks.

## License Readiness - MIT

EvoSpec is licensed under the MIT License, declared through PEP 639 project metadata and distributed in the root `LICENSE` file. `THIRD_PARTY_NOTICES.md` records provenance and remains alongside the project license.
