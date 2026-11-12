from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
import tomllib


@dataclass(frozen=True, slots=True)
class GitResult:
    returncode: int
    stdout: str
    stderr: str


def validate_release_identity(repository: Path) -> str | None:
    """Return an error when the repository lacks the exact release tag identity."""
    version = _project_version(repository)
    if version is None:
        return "pyproject.toml must define project.version as a string"

    tag_name = f"v{version}"
    tag_reference = f"refs/tags/{tag_name}"
    tag_type = _git(repository, "cat-file", "-t", tag_reference)
    if tag_type.returncode != 0:
        return f"missing release tag {tag_name}"
    if tag_type.stdout.strip() != "tag":
        return f"release tag {tag_name} must be annotated"

    tagged_commit = _git(repository, "rev-parse", f"{tag_reference}^{{commit}}")
    head_commit = _git(repository, "rev-parse", "HEAD")
    if tagged_commit.returncode != 0 or head_commit.returncode != 0:
        return "release identity requires a Git HEAD and readable tag target"
    if tagged_commit.stdout.strip() != head_commit.stdout.strip():
        return f"release tag {tag_name} must point at HEAD"
    return None


def _project_version(repository: Path) -> str | None:
    try:
        metadata = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    project = metadata.get("project")
    match project:
        case {"version": str(version)}:
            return version
        case _:
            return None


def _git(repository: Path, *arguments: str) -> GitResult:
    try:
        completed = subprocess.run(
            ("git", *arguments),
            capture_output=True,
            check=False,
            cwd=repository,
            text=True,
        )
    except FileNotFoundError:
        return GitResult(127, "", "git executable not found")
    return GitResult(completed.returncode, completed.stdout, completed.stderr)


def main() -> int:
    """Validate the release tag for the current repository without mutating Git state."""
    error = validate_release_identity(Path.cwd())
    if error is None:
        return 0
    print(error, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
