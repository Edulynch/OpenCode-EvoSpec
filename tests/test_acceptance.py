from __future__ import annotations

import unittest
from pathlib import Path


class AcceptanceTests(unittest.TestCase):
    def test_runtime_surface_is_limited_to_planned_standard_library_modules(self) -> None:
        # Given: the implemented package shape
        package = Path("evospec")

        # When: runtime modules are enumerated
        actual = {item.name for item in package.glob("*.py")}

        # Then: the workflow remains local with only the provider-neutral adapter boundary
        self.assertEqual(
            actual,
            {
                "__init__.py",
                "agent.py",
                "agent_protocol.py",
                "agent_protocol_schema.py",
                "cli.py",
                "opencode_integration.py",
                "dashboard.py",
                "discovery.py",
                "domain.py",
                "guidance.py",
                "lifecycle.py",
                "persistence.py",
                "policies.py",
                "ports.py",
                "services.py",
                "workflow.py",
            },
        )
