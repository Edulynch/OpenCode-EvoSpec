# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Build a host-native bundle into a clean output directory."""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Final


ROOT = Path(__file__).parents[1]
SPECIFICATION = ROOT / "packaging" / "evospec.spec"
PLATFORMS: Final = frozenset(
    {"windows-x86_64", "linux-x86_64", "macos-arm64", "macos-x86_64"}
)
EXECUTABLE_STEMS: Final = frozenset(
    {"evospec", "evospec-agent", "evospec-agent-protocol"}
)


def host_platform(system: str, machine: str) -> str:
    """Return the supported release platform represented by a host."""
    normalized_machine = machine.casefold()
    match system, normalized_machine:
        case "Windows", "amd64" | "x86_64":
            return "windows-x86_64"
        case "Linux", "x86_64" | "amd64":
            return "linux-x86_64"
        case "Darwin", "arm64" | "aarch64":
            return "macos-arm64"
        case "Darwin", "x86_64" | "amd64":
            return "macos-x86_64"
        case _:
            raise ValueError(f"unsupported native build host: {system} {machine}")


def executable_suffix(target: str) -> str:
    """Return the executable suffix required by a supported target."""
    if target not in PLATFORMS:
        raise ValueError(f"unsupported native target: {target}")
    return ".exe" if target == "windows-x86_64" else ""


def expected_public_executables(target: str) -> frozenset[str]:
    """Return the exact public executable names for a supported target."""
    suffix = executable_suffix(target)
    return frozenset(f"{stem}{suffix}" for stem in EXECUTABLE_STEMS)


def validate_public_layout(bundle: Path, target: str) -> frozenset[str]:
    """Return the public executables when the bundle has its exact public layout."""
    suffix = executable_suffix(target)
    executables = frozenset(
        path.name
        for path in bundle.iterdir()
        if path.is_file() and (path.suffix == suffix if suffix else path.stat().st_mode & 0o111)
    )
    expected = expected_public_executables(target)
    if executables != expected:
        raise ValueError(
            f"unexpected public executable layout: {sorted(executables)}"
        )
    return executables


def build_native_bundle(output: Path, target: str | None = None) -> Path:
    """Build a clean shared onedir collection and validate its public executables."""
    actual_target = host_platform(platform.system(), platform.machine())
    if target is not None and target != actual_target:
        raise ValueError(f"native build target {target} does not match host {actual_target}")
    destination = output.resolve()
    bundle = destination / "evospec"
    if bundle.exists():
        shutil.rmtree(bundle)
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="evospec-pyinstaller-") as temporary:
        subprocess.run(
            (
                "uvx",
                "--from",
                "pyinstaller==6.16.0",
                "pyinstaller",
                "--noconfirm",
                "--clean",
                "--distpath",
                str(destination),
                "--workpath",
                str(Path(temporary) / "work"),
                str(SPECIFICATION),
            ),
            check=True,
            cwd=ROOT,
        )
    validate_public_layout(bundle, actual_target)
    return bundle


def main() -> int:
    """Build the bundle selected by the command line."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(tempfile.gettempdir()) / "evospec-native-bundle",
    )
    parser.add_argument("--platform", choices=sorted(PLATFORMS))
    arguments = parser.parse_args()
    print(build_native_bundle(arguments.output, arguments.platform))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
