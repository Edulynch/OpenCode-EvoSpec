# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Audit CI-built release artifacts and exercise installed wheels outside the checkout."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from tests._tempdir import TemporaryDirectory

_SCRIPTS = ("evospec", "evospec-agent", "evospec-agent-protocol")


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    """Inputs shared by isolated installed-wheel exercises."""

    workspace: Path
    checkout: Path
    environment: Mapping[str, str]
    expected_version: str


def _artifact_directory() -> Path:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", required=True, type=Path)
    return parser.parse_args().artifacts.resolve()


def main() -> None:
    """Audit built wheel and sdist artifacts, then exercise their installed runtime surfaces."""
    checkout = Path(__file__).resolve().parents[1]
    expected_version = tomllib.loads((checkout / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    artifacts = _artifact_directory()
    wheel, = tuple(artifacts.glob("*.whl"))
    sdist, = tuple(artifacts.glob("*.tar.gz"))
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    with TemporaryDirectory(prefix="evospec-smoke-") as temporary:
        workspace = Path(temporary)
        _audit_sdist(sdist, checkout, "evospec", expected_version)
        source = workspace / "sdist-source"
        with tarfile.open(sdist) as archive:
            archive.extractall(source)
        extracted, = tuple(source.iterdir())
        rebuilt_directory = workspace / "rebuilt-wheel"
        _ = subprocess.run(
            ("uv", "build", "--wheel", "--out-dir", str(rebuilt_directory)),
            cwd=extracted,
            check=True,
        )
        rebuilt_wheel, = tuple(rebuilt_directory.glob("*.whl"))
        _audit_wheel(wheel, checkout, expected_version)
        _audit_wheel(rebuilt_wheel, checkout, expected_version)
        context = RuntimeContext(workspace, checkout, environment, expected_version)
        _exercise_wheel(wheel, context)
        _exercise_wheel(rebuilt_wheel, context)


def _package_files(checkout: Path) -> set[str]:
    """List distributable package code and resources from the authoritative checkout."""
    return {
        path.relative_to(checkout).as_posix()
        for path in (checkout / "evospec").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


def _audit_sdist(sdist: Path, checkout: Path, expected_name: str, expected_version: str) -> None:
    """Ensure the source archive retains package files and matching metadata."""
    with tarfile.open(sdist) as archive:
        contents = {"/".join(Path(member.name).parts[1:]) for member in archive.getmembers()}
        metadata_member = next(member for member in archive.getmembers() if Path(member.name).name == "PKG-INFO")
        metadata_file = archive.extractfile(metadata_member)
        if metadata_file is None:
            raise AssertionError("sdist PKG-INFO could not be extracted")
        metadata = Parser().parsestr(metadata_file.read().decode("utf-8"))
    missing = _package_files(checkout) | {"LICENSE", "pyproject.toml", "PKG-INFO"}
    missing -= contents
    if missing:
        raise AssertionError(f"sdist omitted: {sorted(missing)}")
    if metadata["Name"] != expected_name or metadata["Version"] != expected_version:
        raise AssertionError("sdist metadata does not match project metadata")
    if metadata["License-Expression"] != "MIT" or metadata["License-File"] != "LICENSE":
        raise AssertionError("sdist metadata does not declare the MIT license")


def _audit_wheel(wheel: Path, checkout: Path, expected_version: str) -> None:
    """Ensure a wheel exposes the declared metadata, entry points, and resources."""
    with zipfile.ZipFile(wheel) as archive:
        contents = set(archive.namelist())
        metadata_file, = tuple(name for name in contents if name.endswith(".dist-info/METADATA"))
        entry_points_file, = tuple(name for name in contents if name.endswith(".dist-info/entry_points.txt"))
        metadata_text = archive.read(metadata_file).decode("utf-8")
        entry_points_text = archive.read(entry_points_file).decode("utf-8")
    missing = _package_files(checkout) - contents
    if missing:
        raise AssertionError(f"wheel omitted: {sorted(missing)}")
    metadata_lines = set(metadata_text.splitlines())
    if "Name: evospec" not in metadata_lines or f"Version: {expected_version}" not in metadata_lines:
        raise AssertionError("wheel metadata does not match project metadata")
    if "License-Expression: MIT" not in metadata_lines or "License-File: LICENSE" not in metadata_lines:
        raise AssertionError("wheel metadata does not declare the MIT license")
    license_file = f"{metadata_file.removesuffix('METADATA')}licenses/LICENSE"
    if license_file not in contents:
        raise AssertionError("wheel omitted its license payload")
    for script in _SCRIPTS:
        if f"{script} = " not in entry_points_text:
            raise AssertionError(f"wheel omitted console entry point {script}")


def _exercise_wheel(wheel: Path, context: RuntimeContext) -> None:
    """Install one wheel in isolation and exercise every declared console entry point."""
    runtime_root = context.workspace / f"{wheel.parent.name}-{wheel.stem}"
    environment_directory = runtime_root / "venv"
    _ = subprocess.run(("uv", "venv", "--python", sys.executable, str(environment_directory)), check=True)
    scripts = environment_directory / ("Scripts" if os.name == "nt" else "bin")
    executable_suffix = ".exe" if os.name == "nt" else ""
    python = scripts / f"python{executable_suffix}"
    _ = subprocess.run(("uv", "pip", "install", "--python", str(python), str(wheel)), check=True)
    workspace = runtime_root / "work"
    workspace.mkdir()

    module = subprocess.run(
        (str(python), "-c", "import evospec; print(evospec.__file__)"),
        capture_output=True,
        check=True,
        cwd=workspace,
        env=context.environment,
        text=True,
    )
    if Path(module.stdout.strip()).resolve().is_relative_to(context.checkout):
        raise AssertionError("installed runtime imported from the source checkout")
    version = subprocess.run(
        (str(scripts / f"evospec{executable_suffix}"), "--version"),
        capture_output=True,
        check=True,
        cwd=workspace,
        env=context.environment,
        text=True,
    )
    if version.stdout.strip() != context.expected_version:
        raise AssertionError("installed console version does not match package metadata")

    root = workspace / "repository cafe\u0301 \u6f22\u5b57"
    root.mkdir()
    _ = subprocess.run(
        (str(scripts / f"evospec{executable_suffix}"), "--project", str(root), "integrate", "opencode"),
        check=True,
        cwd=workspace,
        env=context.environment,
    )
    plugin = root / ".opencode" / "plugins" / "evospec" / "index.ts"
    manifest = root / ".evospec" / "opencode-resources.json"
    if not plugin.is_file() or not manifest.is_file():
        raise AssertionError("installed wheel did not provide its packaged resources")
    if not plugin.read_text(encoding="utf-8").startswith("// EvoSpec-managed: opencode-v2"):
        raise AssertionError("installed wheel did not package the OpenCode V2 plugin")
    if (root / "opencode.json").exists() or (root / ".opencode" / "package.json").exists():
        raise AssertionError("OpenCode integration created an unrelated dependency or config file")
    dashboard_resources = ("index.html", "dashboard.css", "dashboard.js")
    resource_check = subprocess.run(
        (str(python), "-c", "from importlib.resources import files; import sys; sys.exit(not all(files('evospec').joinpath('resources', 'dashboard', name).is_file() for name in ('index.html', 'dashboard.css', 'dashboard.js')))"),
        cwd=workspace,
        env=context.environment,
    )
    if resource_check.returncode != 0:
        raise AssertionError(f"installed wheel omitted dashboard resources: {dashboard_resources}")
    for command in _SCRIPTS[1:]:
        response = subprocess.run(
            (str(scripts / f"{command}{executable_suffix}"), "--root", str(root)),
            capture_output=True,
            check=True,
            cwd=workspace,
            env=context.environment,
            input='{"action":"context"}',
            text=True,
        )
        payload = json.loads(response.stdout)
        if payload["result"]["code"] != "INIT_REQUIRED":
            raise AssertionError(f"{command} did not require installed lifecycle initialization")
        if payload["context"]["initialization_state"] != "UNINITIALIZED":
            raise AssertionError(f"{command} did not expose installed initialization state")
        if payload["context"]["legal_actions"] != ["init", "context"]:
            raise AssertionError(f"{command} did not expose initialization as the installed recovery action")

    _ = subprocess.run(
        (str(scripts / f"evospec{executable_suffix}"), "--project", str(root), "init"),
        check=True,
        cwd=workspace,
        env=context.environment,
    )
    for command in _SCRIPTS[1:]:
        response = subprocess.run(
            (str(scripts / f"{command}{executable_suffix}"), "--root", str(root)),
            capture_output=True,
            check=True,
            cwd=workspace,
            env=context.environment,
            input='{"action":"context"}',
            text=True,
        )
        payload = json.loads(response.stdout)
        if payload["result"]["code"] != "STATUS":
            raise AssertionError(f"{command} did not return the installed protocol status after initialization")
        if payload["context"]["initialization_state"] != "INITIALIZED":
            raise AssertionError(f"{command} did not preserve initialized installed context")


if __name__ == "__main__":
    main()
