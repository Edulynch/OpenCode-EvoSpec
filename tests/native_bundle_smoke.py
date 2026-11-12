# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Exercise a copied host-native bundle without a Python runtime on its environment path."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from tests._tempdir import TemporaryDirectory

from scripts.build_native_bundle import (
    PLATFORMS,
    executable_suffix,
    host_platform,
    validate_public_layout,
)


def main() -> int:
    """Copy the bundle outside the checkout and exercise every public executable."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--platform", choices=sorted(PLATFORMS))
    arguments = parser.parse_args()
    bundle = arguments.bundle.resolve()
    target = host_platform(platform.system(), platform.machine())
    if arguments.platform is not None and arguments.platform != target:
        raise ValueError(f"native smoke target {arguments.platform} does not match host {target}")
    suffix = executable_suffix(target)
    with TemporaryDirectory(prefix="evospec-native-smoke-") as temporary:
        copied_bundle = Path(temporary) / "bundle"
        shutil.copytree(bundle, copied_bundle)
        pythonless_path = copied_bundle.parent / "pythonless-path"
        pythonless_path.mkdir()
        _verify_layout(copied_bundle, target)
        _verify_version(copied_bundle / f"evospec{suffix}", copied_bundle, pythonless_path)
        _verify_dashboard_resources(copied_bundle)
        workspace = copied_bundle.parent / "workspace"
        workspace.mkdir()
        _verify_integration(copied_bundle, workspace, pythonless_path, suffix)
        _verify_protocols(copied_bundle, workspace, pythonless_path, suffix)
    return 0


def _native_environment(pythonless_path: Path) -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if name.upper() not in {"PATH", "PYTHONHOME", "PYTHONPATH"}
    }
    environment["PATH"] = str(pythonless_path)
    aliases = ("python", "pip", "python3", "pip3", *(("py",) if os.name == "nt" else ()))
    for alias in aliases:
        resolved = shutil.which(alias, path=environment["PATH"])
        if resolved is not None:
            raise AssertionError(f"native child PATH resolves {alias} to {resolved}")
    return environment


def _verify_layout(bundle: Path, target: str) -> None:
    try:
        _ = validate_public_layout(bundle, target)
    except ValueError as error:
        raise AssertionError(str(error)) from error


def _verify_version(executable: Path, cwd: Path, pythonless_path: Path) -> None:
    result = subprocess.run(
        (str(executable), "--version"),
        check=True,
        capture_output=True,
        cwd=cwd,
        env=_native_environment(pythonless_path),
        text=True,
    )
    if not result.stdout.strip():
        raise AssertionError("native evospec --version produced no version")


def _verify_dashboard_resources(bundle: Path) -> None:
    resources = bundle / "_internal" / "evospec" / "resources" / "dashboard"
    missing = tuple(name for name in ("index.html", "dashboard.css", "dashboard.js") if not (resources / name).is_file())
    if missing:
        raise AssertionError(f"native bundle omitted dashboard resources: {missing}")


def _verify_protocols(bundle: Path, workspace: Path, pythonless_path: Path, suffix: str) -> None:
    request = json.dumps({"action": "context"})
    for name in (f"evospec-agent{suffix}", f"evospec-agent-protocol{suffix}"):
        result = subprocess.run(
            (str(bundle / name), "--root", str(workspace)),
            check=True,
            capture_output=True,
            cwd=workspace.parent,
            env=_native_environment(pythonless_path),
            input=request,
            text=True,
        )
        payload = json.loads(result.stdout)
        if payload["result"]["code"] != "INIT_REQUIRED":
            raise AssertionError(f"{name} did not require lifecycle initialization")
        if payload["context"]["initialization_state"] != "UNINITIALIZED":
            raise AssertionError(f"{name} did not expose initialization state")
        if payload["context"]["legal_actions"] != ["init", "context"]:
            raise AssertionError(f"{name} did not expose initialization as the recovery action")

    subprocess.run(
        (str(bundle / f"evospec{suffix}"), "--project", str(workspace), "init"),
        check=True,
        cwd=workspace.parent,
        env=_native_environment(pythonless_path),
    )
    for name in (f"evospec-agent{suffix}", f"evospec-agent-protocol{suffix}"):
        result = subprocess.run(
            (str(bundle / name), "--root", str(workspace)),
            check=True,
            capture_output=True,
            cwd=workspace.parent,
            env=_native_environment(pythonless_path),
            input=request,
            text=True,
        )
        payload = json.loads(result.stdout)
        if payload["result"]["code"] != "STATUS":
            raise AssertionError(f"{name} did not return protocol status after initialization")
        if payload["context"]["initialization_state"] != "INITIALIZED":
            raise AssertionError(f"{name} did not preserve initialized context")


def _verify_integration(bundle: Path, workspace: Path, pythonless_path: Path, suffix: str) -> None:
    subprocess.run(
        (str(bundle / f"evospec{suffix}"), "--project", str(workspace), "integrate", "opencode"),
        check=True,
        cwd=workspace.parent,
        env=_native_environment(pythonless_path),
    )
    plugin_path = workspace / ".opencode" / "plugins" / "evospec" / "index.ts"
    plugin = plugin_path.read_text(encoding="utf-8")
    manifest = json.loads((workspace / ".evospec" / "opencode-resources.json").read_text(encoding="utf-8"))
    expected_agent = str(bundle / f"evospec-agent{suffix}")
    match = re.search(r"const AGENT_COMMAND = (\[[^\n]+\])", plugin)
    if match is None:
        raise AssertionError("generated V2 plugin does not declare AGENT_COMMAND as JSON")
    agent_command = json.loads(match.group(1))
    if (
        not isinstance(agent_command, list)
        or len(agent_command) != 1
        or not isinstance(agent_command[0], str)
        or not agent_command[0]
    ):
        raise AssertionError("generated AGENT_COMMAND must contain exactly one nonempty string")
    generated_agent = Path(agent_command[0])
    if not generated_agent.exists():
        raise AssertionError("generated V2 plugin references a missing native agent")
    if generated_agent.name != Path(expected_agent).name:
        raise AssertionError("generated V2 plugin references the wrong native agent basename")
    if not os.path.samefile(generated_agent, expected_agent):
        raise AssertionError("generated V2 plugin does not reference the sibling native agent")
    cli_match = re.search(r"const CLI_COMMAND = (\[[^\n]+\])", plugin)
    if cli_match is None:
        raise AssertionError("generated V2 plugin does not declare CLI_COMMAND as JSON")
    cli_command = json.loads(cli_match.group(1))
    expected_cli = bundle / f"evospec{suffix}"
    if len(cli_command) != 1 or not isinstance(cli_command[0], str) or not os.path.samefile(cli_command[0], expected_cli):
        raise AssertionError("generated V2 plugin does not reference the sibling native CLI")
    if "evospec.agent_protocol" in plugin:
        raise AssertionError("generated V2 plugin still invokes the Python protocol module")
    if set(manifest["resources"]) != {
        ".opencode/plugins/evospec/index.ts",
        ".opencode/plugins/evospec/continuation.mjs",
        ".opencode/skills/evospec/SKILL.md",
        *(f".opencode/commands/evospec.{name}.md" for name in (
            "clarify", "continue", "converge", "context", "doctor", "init", "review", "start", "status",
        )),
    }:
        raise AssertionError("native integration manifest omitted or added an unexpected resource")


if __name__ == "__main__":
    raise SystemExit(main())
