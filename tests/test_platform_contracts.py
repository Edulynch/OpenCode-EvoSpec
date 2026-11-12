from __future__ import annotations

import json
import unittest
from pathlib import Path

from evospec.agent_protocol import execute
from evospec.discovery import DiscoveryPolicy
from evospec.domain import Feature, FeatureId
from evospec.opencode_integration import MANIFEST, PLUGIN, SKILL, integrate
from evospec.persistence import StateStore
from evospec.services import CoreService
from tests._tempdir import TemporaryDirectory


class PlatformContractTests(unittest.TestCase):
    def test_unicode_and_space_root_supports_persistence_recovery_discovery_protocol_and_installation(
        self,
    ) -> None:
        # Given: a host-native root whose name requires Unicode and space handling
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "repository cafe\u0301 \u6f22\u5b57"
            root.mkdir()
            _ = (root / "AGENTS.md").write_text("project instructions", encoding="utf-8")
            _ = (root / "README.md").write_text("project documentation", encoding="utf-8")
            service = CoreService.open(root)

            # When: every filesystem-facing public surface uses that root
            saved = service.record_feature(
                service.recover(), Feature(FeatureId("feature-platform"), "Platform", None)
            )
            recovered = CoreService.open(root).recover()
            evidence = DiscoveryPolicy(root).discover()
            installation = integrate(root)

            # Then: state, discovery, protocol, and installed resources remain root-contained
            self.assertEqual(StateStore(root).load_state(), saved)
            self.assertEqual(recovered, saved)
            self.assertEqual(
                tuple(item.path.value for item in evidence), ("AGENTS.md", "README.md")
            )
            self.assertEqual(
                json.loads(execute(root, '{"action":"context"}'))["result"]["code"],
                "INIT_REQUIRED",
            )
            self.assertEqual(installation.conflicts, ())
            self.assertTrue((root / PLUGIN).is_file())
            self.assertTrue((root / SKILL).is_file())
            self.assertTrue((root / MANIFEST).is_file())
