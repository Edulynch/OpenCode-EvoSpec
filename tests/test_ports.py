from __future__ import annotations

import unittest
from pathlib import Path

from evospec.persistence import StateStore
from evospec.ports import StatePersistence
from tests._tempdir import TemporaryDirectory


class PortTests(unittest.TestCase):
    def test_state_store_satisfies_read_only_protocol(self) -> None:
        with TemporaryDirectory() as temporary:
            store = StateStore(Path(temporary))

            self.assertIsInstance(store, StatePersistence)
            self.assertFalse(hasattr(store, "save_state"))
