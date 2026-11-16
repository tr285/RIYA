"""Automated tests for RIYA's voice conversation state machine."""

from __future__ import annotations

import threading
import unittest

from core.voice.errors import InvalidStateTransition
from core.voice.state import ACTIVE_STATES, VoiceState, VoiceStateMachine


class TestVoiceStateMachine(unittest.TestCase):
    def test_required_states_exist(self):
        expected = {
            "IDLE",
            "LISTENING",
            "TRANSCRIBING",
            "THINKING",
            "SPEAKING",
            "INTERRUPTED",
            "ERROR",
            "STOPPING",
        }
        self.assertEqual({s.name for s in VoiceState}, expected)

    def test_valid_conversation_and_interruption_transitions(self):
        sm = VoiceStateMachine()
        self.assertEqual(sm.state, VoiceState.IDLE)
        self.assertFalse(sm.is_active())

        sm.transition(VoiceState.LISTENING)
        self.assertTrue(sm.is_active())
        sm.transition(VoiceState.TRANSCRIBING)
        sm.transition(VoiceState.THINKING)
        sm.transition(VoiceState.SPEAKING)
        sm.transition(VoiceState.INTERRUPTED)
        sm.transition(VoiceState.LISTENING)
        sm.transition(VoiceState.TRANSCRIBING)
        sm.transition(VoiceState.THINKING)
        sm.transition(VoiceState.SPEAKING)
        sm.transition(VoiceState.LISTENING)
        sm.transition(VoiceState.STOPPING)
        sm.transition(VoiceState.IDLE)
        self.assertFalse(sm.is_active())

    def test_any_active_state_can_stop_and_error_recovers_to_idle(self):
        for active in ACTIVE_STATES:
            sm = VoiceStateMachine(initial=active)
            sm.transition(VoiceState.STOPPING)
            sm.transition(VoiceState.IDLE)
            self.assertEqual(sm.state, VoiceState.IDLE)

            err_sm = VoiceStateMachine(initial=active)
            err_sm.transition(VoiceState.ERROR)
            with self.assertRaises(InvalidStateTransition):
                err_sm.transition(VoiceState.LISTENING)
            err_sm.transition(VoiceState.IDLE)
            self.assertEqual(err_sm.state, VoiceState.IDLE)

    def test_invalid_transitions_rejected(self):
        sm = VoiceStateMachine()
        for invalid in (
            VoiceState.TRANSCRIBING,
            VoiceState.THINKING,
            VoiceState.SPEAKING,
            VoiceState.INTERRUPTED,
            VoiceState.STOPPING,
            VoiceState.ERROR,
        ):
            with self.assertRaises(InvalidStateTransition):
                sm.transition(invalid)
        self.assertEqual(sm.state, VoiceState.IDLE)

    def test_listener_isolation_and_concurrent_try_transition(self):
        observed: list[tuple[VoiceState, VoiceState, str]] = []
        sm = VoiceStateMachine()

        def failing_listener(prev, curr, reason):
            raise RuntimeError("UI callback failed")

        def good_listener(prev, curr, reason):
            observed.append((prev, curr, reason))

        sm.add_listener(failing_listener)
        sm.add_listener(good_listener)
        sm.transition(VoiceState.LISTENING, "start")
        self.assertEqual(observed, [(VoiceState.IDLE, VoiceState.LISTENING, "start")])

        # Only one concurrent thread may claim LISTENING -> TRANSCRIBING.
        winners: list[bool] = []
        barrier = threading.Barrier(4)

        def worker():
            barrier.wait()
            winners.append(sm.try_transition(VoiceState.TRANSCRIBING, "race"))

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(winners.count(True), 1)
        self.assertEqual(sm.state, VoiceState.TRANSCRIBING)


if __name__ == "__main__":
    unittest.main()
