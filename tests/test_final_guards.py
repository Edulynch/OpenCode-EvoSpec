from __future__ import annotations

import json
import unittest
from pathlib import Path

from evospec.domain import State
from evospec.persistence import RecoveryError, StateStore
from tests._tempdir import TemporaryDirectory


class FinalGuardTests(unittest.TestCase):
    def test_recovery_rejects_unknown_state_and_context_fields(self) -> None:
        # Given: an otherwise shaped state containing unknown authority data
        with TemporaryDirectory() as temporary:
            state_path = Path(temporary) / ".evospec" / "state.json"
            state_path.parent.mkdir()
            state_path.write_text(json.dumps({"schema": 1, "revision": 0, "active_context": None, "preserved_primary_context": None, "publications": [], "completed_side_contexts": [], "unexpected": True}), encoding="utf-8")

            # When/Then: exact schemas do not admit silent future fields
            with self.assertRaises(RecoveryError):
                StateStore(Path(temporary)).load_state()
