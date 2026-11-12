# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Create deterministic, checksummed native release archives.

Run with: uv run scripts/assemble_release_assets.py --output dist/release --bundle windows-x86_64=path/to/bundle ...
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import tomllib
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Final

PLATFORMS: Final = (
    "windows-x86_64",
    "linux-x86_64",
    "macos-arm64",
    "macos-x86_64",
)
RELEASE_PLATFORMS: Final = ("windows-x86_64", "linux-x86_64")
ZIP_TIMESTAMP: Final = (1980, 1, 1, 0, 0, 0)
EXECUTABLE_STEMS: Final = frozenset(
    {"evospec", "evospec-agent", "evospec-agent-protocol"}
)


def assemble_release_assets(
    version: str, bundles: Mapping[str, Path], output: Path
) -> tuple[Path, ...]:
    """Archive every supported bundle and write its exact SHA256 allowlist."""
    if frozenset(bundles) != frozenset(RELEASE_PLATFORMS):
        raise ValueError("release bundles must contain each supported platform exactly once")
    if not version or "/" in version or "\\" in version:
        raise ValueError("release version must be a non-empty filename component")
    _ = output.mkdir(parents=True, exist_ok=True)
    artifacts: list[Path] = []
    for platform in sorted(RELEASE_PLATFORMS):
        bundle = bundles[platform]
        _validate_bundle(bundle)
        artifact = output / f"evospec-{version}-{platform}.zip"
        _write_archive(bundle, artifact)
        artifacts.append(artifact)
    manifest = "".join(
        f"{hashlib.sha256(artifact.read_bytes()).hexdigest()}  {artifact.name}\n"
        for artifact in artifacts
    )
    (output / "SHA256SUMS").write_text(manifest, encoding="utf-8", newline="\n")
    return tuple(artifacts)


def assemble_platform_asset(version: str, platform: str, bundle: Path, output: Path) -> Path:
    """Create and validate exactly one platform-specific native release archive."""
    _validate_version(version)
    _validate_platform(platform)
    _validate_bundle(bundle)
    _validate_public_layout(bundle, platform)
    _ = output.mkdir(parents=True, exist_ok=True)
    artifact = _artifact_path(version, platform, output)
    _write_archive(bundle, artifact)
    validate_platform_asset(version, platform, output)
    return artifact


def validate_platform_asset(version: str, platform: str, output: Path) -> None:
    """Require one safe platform archive with its exact public executable layout."""
    _validate_version(version)
    _validate_platform(platform)
    artifact = _artifact_path(version, platform, output)
    archives = sorted(output.glob("*.zip"))
    if archives != [artifact]:
        raise ValueError(
            f"expected exactly one native archive named {artifact.name}, found {[path.name for path in archives]}"
        )
    with zipfile.ZipFile(artifact) as archive:
        members = archive.infolist()
    names = [member.filename for member in members]
    if not names or len(names) != len(set(names)):
        raise ValueError("native archive has empty or duplicate paths")
    for name in names:
        parts = name.split("/")
        if (
            "\\" in name
            or name.endswith("/")
            or not name.startswith("evospec/")
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ValueError(f"native archive has unsafe path: {name}")
    public = frozenset(
        member.filename.removeprefix("evospec/")
        for member in members
        if "/" not in member.filename.removeprefix("evospec/")
        and _is_public_executable(member, platform)
    )
    if public != _expected_public_executables(platform):
        raise ValueError(f"unexpected native archive public layout: {sorted(public)}")


def _validate_bundle(bundle: Path) -> None:
    """Reject bundle layouts that cannot become a safe archive."""
    if not bundle.is_dir() or bundle.is_symlink():
        raise ValueError(f"bundle is not a regular directory: {bundle}")
    for path in bundle.rglob("*"):
        relative = path.relative_to(bundle)
        if path.is_symlink() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError(f"bundle contains unsafe path: {path}")
        if not path.is_dir() and not path.is_file():
            raise ValueError(f"bundle contains unsupported entry: {path}")


def _validate_public_layout(bundle: Path, platform: str) -> None:
    """Require the bundle's root executables to match the target's public names."""
    public = frozenset(
        path.name
        for path in bundle.iterdir()
        if path.is_file()
        and (
            path.suffix == ".exe"
            if platform == "windows-x86_64"
            else bool(path.stat().st_mode & 0o111)
        )
    )
    if public != _expected_public_executables(platform):
        raise ValueError(f"unexpected public executable layout: {sorted(public)}")


def _is_public_executable(member: zipfile.ZipInfo, platform: str) -> bool:
    """Return whether a root ZIP member is a target public executable."""
    return (
        member.filename.endswith(".exe")
        if platform == "windows-x86_64"
        else bool(member.external_attr >> 16 & 0o111)
    )


def _expected_public_executables(platform: str) -> frozenset[str]:
    """Return the exact public executable names for one supported platform."""
    _validate_platform(platform)
    suffix = ".exe" if platform == "windows-x86_64" else ""
    return frozenset(f"{stem}{suffix}" for stem in EXECUTABLE_STEMS)


def _write_archive(bundle: Path, artifact: Path) -> None:
    """Write a byte-stable ZIP containing one top-level evospec directory."""
    temporary = artifact.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in sorted(path for path in bundle.rglob("*") if path.is_file()):
            member = Path("evospec") / source.relative_to(bundle)
            info = zipfile.ZipInfo(member.as_posix(), date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100755 << 16 if source.stat().st_mode & 0o111 else 0o100644 << 16
            archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    _ = shutil.move(temporary, artifact)


def _artifact_path(version: str, platform: str, output: Path) -> Path:
    """Return the public archive path for one platform and version."""
    return output / f"evospec-{version}-{platform}.zip"


def _validate_version(version: str) -> None:
    """Reject versions that cannot safely form public archive names."""
    if not version or "/" in version or "\\" in version:
        raise ValueError("release version must be a non-empty filename component")


def _validate_platform(platform: str) -> None:
    """Reject unsupported public platform names."""
    if platform not in PLATFORMS:
        raise ValueError(f"unsupported release platform: {platform}")


def _metadata_version(repository: Path) -> str:
    """Read the authoritative package version from project metadata."""
    metadata = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]
    version = project["version"]
    if not isinstance(version, str):
        raise TypeError("pyproject project.version must be a string")
    return version


def _parse_bundle(value: str) -> tuple[str, Path]:
    """Parse one platform=directory command-line assignment."""
    platform, separator, directory = value.partition("=")
    if not separator or not platform or not directory:
        raise ValueError("bundle must be written as platform=directory")
    return platform, Path(directory)


def main() -> int:
    """Assemble release assets from native bundle directories."""
    parser = argparse.ArgumentParser()
    _ = parser.add_argument("--output", required=True, type=Path)
    _ = parser.add_argument("--bundle", action="append")
    _ = parser.add_argument("--platform", choices=PLATFORMS)
    _ = parser.add_argument("--validate-platform-asset", action="store_true")
    arguments = parser.parse_args()
    version = _metadata_version(Path(__file__).parents[1])
    if arguments.validate_platform_asset:
        if arguments.platform is None or arguments.bundle is not None:
            parser.error("--validate-platform-asset requires --platform and no --bundle")
        validate_platform_asset(version, arguments.platform, arguments.output)
        return 0
    if arguments.platform is not None:
        if arguments.bundle is None or len(arguments.bundle) != 1:
            parser.error("--platform requires exactly one --bundle directory")
        _ = assemble_platform_asset(version, arguments.platform, Path(arguments.bundle[0]), arguments.output)
        return 0
    if arguments.bundle is None:
        parser.error("--bundle is required when --platform is omitted")
    parsed = [_parse_bundle(value) for value in arguments.bundle]
    bundles = dict(parsed)
    if len(bundles) != len(parsed):
        raise ValueError("each platform may be supplied only once")
    _ = assemble_release_assets(version, bundles, arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
