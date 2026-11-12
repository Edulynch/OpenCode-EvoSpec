# GitHub-hosted CI

The Phase 4C workflow (`.github/workflows/phase4c-validation.yml`) uses GitHub-hosted runners only:
`ubuntu-22.04` and `windows-2022`. It keeps `contents: read`, SHA-pinned actions, and the existing
push, pull-request, and manual-dispatch triggers.

## Workflow behavior

- Validation runs Python 3.11 source tests, type checks, release-identity checks for tags, builds, and
  installed-artifact smoke tests on both operating systems.
- Native jobs build and test Linux x86_64 and Windows x86_64 bundles, then upload the validated
  platform archives.
- Pushes, same-repository pull requests, and full manual dispatches assemble a five-file release
  candidate with `SHA256SUMS`. Fork pull requests do not run that assembly job. The workflow creates
  an artifact only; it does not publish a release or tag.
- Manual dispatch defaults to the full workflow. Set `smoke_only` to `true` to run only the short
  Linux and Windows runner checks. They verify OS, X64 architecture, and the `github-hosted`
  environment, and print runner identity metadata without secrets.

## Historical self-hosted runners

The workflow no longer selects or depends on self-hosted runners. This CI migration does not delete
or deregister any existing runner; runner lifecycle and maintenance remain separate from workflow
execution.
