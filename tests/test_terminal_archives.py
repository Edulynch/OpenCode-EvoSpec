from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from evospec.domain import Change, ChangeId, Feature
from evospec.lifecycle import LifecycleState, Phase
from evospec.services import CoreService, ServiceError
from tests._tempdir import TemporaryDirectory
from tests.test_services_state import (
    advance_context,
    implementation_context,
    proposed_context,
)


class TerminalArchiveTests(unittest.TestCase):
    def test_archive_requires_authoritative_terminal_identity(self) -> None:
        # Given: active work and a caller-recorded terminal version of it
        with TemporaryDirectory() as temporary:
            service = CoreService.open(Path(temporary))
            active = implementation_context("feature", "change")
            state = service.record_feature(service.recover(), Feature(active.feature_id, "Feature", None))
            change = Change(ChangeId("change"), active.feature_id, "Change")
            state = service.record_change(state, change)
            state = advance_context(service, state, active)
            terminal = replace(active, status=LifecycleState.ABANDONED)

            # When: caller archives its terminal trace
            archived = service.archive_terminal(state, terminal)

            # Then: identity and terminal phase remain inspectable
            self.assertEqual(archived.terminal_archives[0].context.change_id, active.change_id)
            with self.assertRaises(ServiceError):
                service.archive_terminal(state, replace(terminal, change_id=None))

    def test_rejected_primary_is_persisted_and_not_recovered_as_active(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreService.open(root)
            base = implementation_context("feature", "change")
            clarifying = replace(
                base,
                status=LifecycleState.CLARIFYING,
                phase=Phase.CLARIFY,
            )
            state = service.record_feature(service.recover(), Feature(base.feature_id, "Feature", None))
            change = Change(ChangeId("change"), base.feature_id, "Change")
            state = service.record_change(state, change)
            state = advance_context(service, state, clarifying)

            state = service.archive_terminal(
                state,
                replace(clarifying, status=LifecycleState.REJECTED),
            )
            recovered = CoreService.open(root).recover()

            self.assertIsNone(recovered.active_context)
            self.assertEqual(recovered.terminal_archives[0].context.status, LifecycleState.REJECTED)
