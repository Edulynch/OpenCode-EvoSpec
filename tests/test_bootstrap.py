from __future__ import annotations

import sys
import tomllib
import unittest
from pathlib import Path


class BootstrapTests(unittest.TestCase):
    def test_project_declares_supported_stdlib_runtime(self) -> None:
        # Given: the checkout metadata
        metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

        # When: project runtime requirements are inspected
        project = metadata["project"]

        # Then: CPython 3.11+ and no runtime package are declared
        self.assertGreaterEqual(sys.version_info[:2], (3, 11))
        self.assertEqual(project["requires-python"], ">=3.11")
        self.assertEqual(project["dependencies"], [])
        self.assertEqual(metadata["build-system"]["build-backend"], "setuptools.build_meta")
        self.assertEqual(project["scripts"]["evospec"], "evospec.cli:main")
        self.assertEqual(project["scripts"]["evospec-agent"], "evospec.agent_protocol:main")
        self.assertEqual(project["scripts"]["evospec-agent-protocol"], "evospec.agent_protocol:main")
        self.assertEqual(
            metadata["tool"]["setuptools"]["package-data"]["evospec"],
            ["resources/opencode/**/*", "resources/dashboard/**/*"],
        )
        self.assertFalse(metadata["tool"]["setuptools"]["include-package-data"])
        self.assertFalse(metadata["tool"]["setuptools"]["packages"]["find"]["namespaces"])
