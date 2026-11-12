from __future__ import annotations

import unittest
from pathlib import Path

import evospec.persistence as persistence
import evospec.services as services
from evospec.domain import Feature, FeatureId
from evospec.persistence import StateStore
from evospec.services import CoreService, ServiceError
from tests._tempdir import TemporaryDirectory
from tests.test_persistence_atomic import FailingReplace


class AuthoritySurfaceTests(unittest.TestCase):
    def test_bound_service_has_no_generic_write_capability(self) -> None:
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))

            for name in (
                "_CoreService__commit_state",
                "_HistoryMutations__commit_state",
                "_HistoryMutations__store_change",
                "_HistoryMutations__store_decision",
                "_HistoryMutations__store_observation",
                "_HistoryMutations__store_drift",
                "_PublicationMutations__commit_state",
                "_PublicationMutations__store_publication",
                "persistence",
                "root",
                "operations",
                "writer",
            ):
                with self.assertRaises(AttributeError):
                    getattr(service, name)
            self.assertFalse(hasattr(service, "__dict__"))

    def test_failure_injection_remains_bound_to_validated_operations(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root, FailingReplace())

            with self.assertRaises(ServiceError):
                service.record_feature(
                    service.recover(), Feature(FeatureId("feature"), "Feature", None)
                )

            self.assertEqual(StateStore(root).load_state().revision, 0)

    def test_runtime_modules_export_no_generic_write_primitive(self) -> None:
        # Given: the only retained modules allowed to participate in mutation
        forbidden = {
            "_commit",
            "_replace_state",
            "_write_record",
            "_write_publication",
            "_target",
            "atomic_write",
        }

        # When/Then: callers cannot import a reusable generic writer
        for module in (persistence, services):
            with self.subTest(module=module.__name__):
                self.assertTrue(forbidden.isdisjoint(vars(module)))

if __name__ == "__main__":
    unittest.main()
