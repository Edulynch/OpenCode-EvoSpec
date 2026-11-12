from __future__ import annotations

import ast
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
WINDOWS_EXECUTABLES = frozenset(
    {"evospec.exe", "evospec-agent.exe", "evospec-agent-protocol.exe"}
)
POSIX_EXECUTABLES = frozenset({"evospec", "evospec-agent", "evospec-agent-protocol"})


class NativeBundleContractTests(unittest.TestCase):
    def test_spec_uses_one_shared_multiprogram_collection_with_resources(self) -> None:
        # Given: the checked-in native bundle specification
        specification = (ROOT / "packaging" / "evospec.spec").read_text(encoding="utf-8")

        # When: its PyInstaller targets are inspected without invoking a build
        tree = ast.parse(specification)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        names = [node.func.id for node in calls if isinstance(node.func, ast.Name)]

        # Then: three launchers share one collection and carry package resources
        self.assertEqual(names.count("Analysis"), 3)
        self.assertEqual(names.count("EXE"), 3)
        self.assertEqual(names.count("MERGE"), 1)
        self.assertEqual(names.count("COLLECT"), 1)
        self.assertIn("evospec/resources/opencode", specification)
        self.assertIn("evospec/resources/dashboard", specification)
        self.assertNotIn("_MEIPASS", specification)

    def test_entrypoints_delegate_to_the_declared_runtime_mains(self) -> None:
        # Given: the thin native entrypoint wrappers
        expected_targets = {
            "evospec_cli.py": ("evospec.cli", "main"),
            "evospec_agent.py": ("evospec.agent_protocol", "main"),
            "evospec_agent_protocol.py": ("evospec.agent_protocol", "main"),
        }

        # When: each wrapper module is parsed
        imported_targets = {
            path.name: {
                (node.module, alias.name)
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                if isinstance(node, ast.ImportFrom) and node.module is not None
                for alias in node.names
            }
            for path in (ROOT / "packaging" / "entrypoints").glob("*.py")
        }

        # Then: no wrapper owns application behavior beyond its declared main
        self.assertEqual(set(imported_targets), set(expected_targets))
        for filename, target in expected_targets.items():
            self.assertIn(target, imported_targets[filename])

    def test_driver_validates_target_architecture_suffix_and_public_layout(self) -> None:
        # Given: platform-specific public native bundle layouts
        from scripts.build_native_bundle import (
            executable_suffix,
            host_platform,
            validate_public_layout,
        )

        # When: each supported host architecture and layout is validated
        self.assertEqual(host_platform("Windows", "AMD64"), "windows-x86_64")
        self.assertEqual(host_platform("Linux", "x86_64"), "linux-x86_64")
        self.assertEqual(host_platform("Darwin", "arm64"), "macos-arm64")
        self.assertEqual(host_platform("Darwin", "x86_64"), "macos-x86_64")
        self.assertEqual(executable_suffix("windows-x86_64"), ".exe")
        self.assertEqual(executable_suffix("linux-x86_64"), "")
        windows = self._layout(WINDOWS_EXECUTABLES, executable=True)
        posix = self._layout(POSIX_EXECUTABLES, executable=True)

        # Then: only the target-specific public executables are accepted
        self.assertEqual(validate_public_layout(windows, "windows-x86_64"), WINDOWS_EXECUTABLES)
        if os.name != "nt":
            self.assertEqual(validate_public_layout(posix, "linux-x86_64"), POSIX_EXECUTABLES)
        with self.assertRaises(ValueError):
            _ = host_platform("Linux", "aarch64")
        with self.assertRaises(ValueError):
            _ = validate_public_layout(self._layout(POSIX_EXECUTABLES | {"unexpected"}, executable=True), "macos-arm64")
        with self.assertRaises(ValueError):
            _ = validate_public_layout(windows, "linux-x86_64")

    def _layout(self, names: frozenset[str], *, executable: bool) -> Path:
        temporary = self.enterContext(self._temporary_directory())
        root = Path(temporary)
        for name in names:
            path = root / name
            path.touch()
            if executable:
                path.chmod(0o755)
        return root

    def _temporary_directory(self):
        from tests._tempdir import TemporaryDirectory

        return TemporaryDirectory()
