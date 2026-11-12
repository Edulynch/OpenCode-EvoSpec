# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Exercise a real Windows native bundle through the local-fixture installer lifecycle.

Run with: uv run tests/native_installer_e2e.py
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from tests._tempdir import TemporaryDirectory

from tests.test_install_ps1 import (
    WINDOWS_GUIDES,
    PowerShellFixture,
    documented_windows_command,
)

ROOT = Path(__file__).parents[1]
PLATFORM = "windows-x86_64"


class InstallerArguments(argparse.Namespace):
    bundle: Path | None = None


def main() -> int:
    """Build a native fixture, install it transactionally, and preserve project integration."""
    if os.name != "nt":
        raise SystemExit("native installer E2E requires Windows")
    parser = argparse.ArgumentParser()
    _ = parser.add_argument("--bundle", type=Path, help="reuse an already-built native bundle")
    arguments = parser.parse_args(namespace=InstallerArguments())
    with TemporaryDirectory(prefix="evospec-installer-e2e-") as temporary:
        workspace = Path(temporary)
        bundle_output = workspace / "bundle-output"
        bundle = arguments.bundle
        if bundle is None:
            _ = subprocess.run(
                (sys.executable, "scripts/build_native_bundle.py", "--output", str(bundle_output)),
                check=True,
                cwd=ROOT,
            )
            bundle = bundle_output / "evospec"
        for guide in WINDOWS_GUIDES:
            _check_documented_install(bundle, guide)
        assets = workspace / "assets"
        _ = assets.mkdir()
        install_root = workspace / "install"
        path_file = workspace / "path.txt"
        project = workspace / "project"
        _ = project.mkdir()
        _write_fixture(assets, bundle, "0.3.0")
        _run_installer(assets, install_root, path_file, "0.3.0")
        current = install_root / "current"
        _ = subprocess.run(
            (str(current / "evospec.exe"), "--project", str(project), "integrate", "opencode"),
            check=True,
            cwd=workspace,
        )
        _run_installer(assets, install_root, path_file, "0.3.0")
        _write_fixture(assets, bundle, "0.3.1")
        _run_installer(assets, install_root, path_file, "0.3.1")
        _ = subprocess.run((str(current / "evospec.exe"), "--version"), check=True, cwd=workspace)
        _run_installer(assets, install_root, path_file, "0.3.1", uninstall=True)
        if current.exists():
            raise AssertionError("uninstall retained the owned current path")
        if not (project / ".opencode" / "plugins" / "evospec" / "index.ts").is_file():
            raise AssertionError("installer lifecycle damaged project integration")
        if str(current) in path_file.read_text(encoding="utf-8"):
            raise AssertionError("uninstall retained the owned PATH record")
    return 0


def _check_documented_install(bundle: Path, guide: str) -> None:
    """Use the Markdown bootstrap, fake GitHub transport, and actual native version."""
    version = subprocess.run(
        (str(bundle / "evospec.exe"), "--version"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not version:
        raise AssertionError("native bundle did not report a version")
    temporary = TemporaryDirectory(prefix="evospec's documented install ")
    workspace = Path(temporary.name)
    with PowerShellFixture(temporary, workspace / "assets", workspace / "install", workspace / "path.txt") as fixture:
        fixture.release_tag = f"v{version}"
        _write_fixture(fixture.assets, bundle, version)
        _ = (fixture.gh_directory / "evospec.cmd").write_text("@echo off\necho 0.0.0\n", encoding="utf-8")
        command = documented_windows_command(guide)
        completed = fixture.run_powershell(
            "$before = $env:PATH\n" + command + "\n"
            "$version = evospec --version\n"
            f"if ($LASTEXITCODE -ne 0 -or $version -ne '{version}') {{ throw \"unexpected native version: $version\" }}\n"
            "Write-Output \"bare evospec --version: $version\"\n"
            "evospec --help\n"
            "if ($LASTEXITCODE -ne 0) { throw 'native help failed' }\n"
            "evospec --invalid-option\n"
            "if ($LASTEXITCODE -eq 0) { throw 'invalid native input accepted' }\n"
            + command + "\n"
            "& $env:EVOSPEC_TEST_INSTALL_SCRIPT -Uninstall\n"
            "if ($env:PATH -cne $before) { throw 'uninstall did not restore unrelated process PATH' }\n"
            "Write-Output 'native same-process lifecycle passed'"
        )
        print(f"{guide}:\n{completed.stdout}", end="")
        print(completed.stderr, file=sys.stderr, end="")
        completed.check_returncode()
        if completed.stdout.splitlines().count(f"EvoSpec {version} installed successfully.") != 2:
            raise AssertionError("expected exactly two documented install success messages")
        if "native same-process lifecycle passed" not in completed.stdout.splitlines():
            raise AssertionError("installer exited the invoking session")
        if tuple(fixture.downloads.iterdir()):
            raise AssertionError("bootstrap retained temporary files")


def _write_fixture(assets: Path, bundle: Path, version: str) -> None:
    """Create one versioned local release archive and its exact checksum manifest."""
    private_extensions = tuple(bundle.glob("_internal/_*.pyd"))
    if not private_extensions:
        raise AssertionError("native fixture is missing a direct PyInstaller private extension")
    for path in assets.glob("*.zip"):
        path.unlink()
    archive = assets / f"evospec-{version}-{PLATFORM}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as release:
        for source in sorted(bundle.rglob("*")):
            if source.is_file():
                release.write(source, Path("evospec") / source.relative_to(bundle))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    _ = (assets / "SHA256SUMS").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")


def _run_installer(
    assets: Path, install_root: Path, path_file: Path, tag: str, *, uninstall: bool = False
) -> None:
    """Run the PowerShell installer only against per-test fixture state."""
    command = [
        "pwsh",
        "-NoProfile",
        "-File",
        str(ROOT / "scripts" / "install.ps1"),
        "-Tag",
        tag,
        "-AssetDirectory",
        str(assets),
        "-ManifestPath",
        str(assets / "SHA256SUMS"),
        "-InstallRoot",
        str(install_root),
        "-PathFile",
        str(path_file),
    ]
    if uninstall:
        command.append("-Uninstall")
    _ = subprocess.run(command, check=True, cwd=assets)


if __name__ == "__main__":
    raise SystemExit(main())
