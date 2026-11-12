# Security

## Scope and releases

Security fixes in this source snapshot apply only to the source distributed from this repository. Package version 0.5.0 is a source/package version, not a release published here; this repository currently has no GitHub Release or native installer assets. Future distribution requires the authorized release and validation gates in [docs/release-readiness.md](docs/release-readiness.md).

## Reporting a concern

Please do not post credentials, personal data, private repository contents, or sensitive exploit details in a public issue or discussion.

If GitHub Security > Report a vulnerability is available for this repository, use that private reporting route. Its availability has not been verified here; do not assume it is enabled. If it is unavailable, open only a minimal, non-sensitive public issue asking the maintainer for a private reporting channel. Do not send sensitive details until a private channel is confirmed. No security contact email is specified.

## Runtime and artifact boundaries

The dashboard is intended for local loopback use and exposes read-only context. It is not designed for network exposure and does not authenticate local processes.

If a future authorized release provides `SHA256SUMS`, it checks archive integrity against the downloaded manifest; it does not independently authenticate the publisher when both come from the same channel. No signing or notarization is claimed. No native assets are currently published here, and the checked-in installer sources are not downloadable release installers.

This document does not assert a readiness approval, change repository visibility, or publish a release.
