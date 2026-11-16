import unittest
from unittest.mock import Mock, patch

class TestConfirmationFlow(unittest.TestCase):

    def setUp(self):
        # Avoid the assistant module's legacy module-level memory helper so
        # this confirmation test never initializes the project database.
        from core.memory.database import MemoryDatabase

        with patch.object(MemoryDatabase, "_initialize"):
            from core.agent.assistant import RIYAAssistant

        # Bypass __init__: do not load AI models or databases.
        self.assistant = RIYAAssistant.__new__(RIYAAssistant)
        self.assistant.agent = Mock()
        self.assistant.pending_plan = None
        self.assistant.pending_action = {
            "tool": "remember",
            "parameters": {
                "category": "personal",
                "key": "my_name",
                "value": "Tukaram",
            },
            "confirmation_token": "test-confirmation-token",
        }
        self.assistant.agent.execute.return_value = {
            "status": "success"
        }
        self.assistant._format_result = Mock(
            return_value="Memory saved."
        )
        self.assistant._update_context = Mock()

    def test_yes_executes_action(self):
        result = self.assistant._handle_confirmation("yes")

        self.assertEqual(result, "Memory saved.")
        self.assistant.agent.execute.assert_called_once_with(
            "remember",
            confirmation_token="test-confirmation-token",
            category="personal",
            key="my_name",
            value="Tukaram",
        )
        self.assertIsNone(self.assistant.pending_action)

    def test_no_cancels_action(self):
        result = self.assistant._handle_confirmation("no")

        self.assertIn("cancelled", result.lower())
        self.assistant.agent.execute.assert_not_called()
        self.assertIsNone(self.assistant.pending_action)

    def test_unclear_response_keeps_pending_action(self):
        result = self.assistant._handle_confirmation("maybe")

        self.assertIn("yes", result.lower())
        self.assertIsNotNone(self.assistant.pending_action)
        self.assistant.agent.execute.assert_not_called()


    def test_no_cancels_pending_plan(self):
        self.assistant.pending_plan = {
            "steps": [],
            "next_index": 1,
            "results": [],
        }

        result = self.assistant._handle_confirmation("no")

        self.assertIn("cancelled", result.lower())
        self.assertIsNone(self.assistant.pending_action)
        self.assertIsNone(self.assistant.pending_plan)
        self.assistant.agent.cancel_confirmation.assert_called_once_with(
            "test-confirmation-token"
        )

    def test_unclear_response_preserves_pending_plan(self):
        plan_state = {
            "steps": [],
            "next_index": 1,
            "results": [],
        }
        self.assistant.pending_plan = plan_state

        result = self.assistant._handle_confirmation("maybe")

        self.assertIn("yes", result.lower())
        self.assertIs(self.assistant.pending_plan, plan_state)
        self.assertIsNotNone(self.assistant.pending_action)
        self.assistant.agent.execute.assert_not_called()


    @staticmethod
    def _step(action):
        from types import SimpleNamespace
        return SimpleNamespace(
            action=action,
            parameters={},
            description=action,
        )

    @staticmethod
    def _confirmation(tool, token):
        return {
            "status": "confirmation_required",
            "tool": tool,
            "parameters": {},
            "confirmation_token": token,
            "reason": "Confirmation required.",
        }

    def test_plan_resumes_after_confirmation(self):
        steps = [
            self._step("first"),
            self._step("second"),
            self._step("third"),
        ]

        self.assistant.agent.execute.side_effect = [
            {"status": "success"},
            self._confirmation("second", "token-1"),
            {"status": "success"},
            {"status": "success"},
        ]
        self.assistant._summarize_results = Mock(
            return_value="Plan completed."
        )

        first_response = self.assistant._execute_plan_steps(
            steps, 0, [], "complete my task"
        )

        self.assertIn("Proceed", first_response)
        self.assertEqual(
            self.assistant.pending_plan["next_index"], 2
        )
        self.assertEqual(
            len(self.assistant.pending_plan["results"]), 1
        )

        response = self.assistant._handle_confirmation("yes")

        self.assertEqual(response, "Plan completed.")
        self.assertEqual(
            [call.args[0] for call in
             self.assistant.agent.execute.call_args_list],
            ["first", "second", "second", "third"],
        )
        self.assertIsNone(self.assistant.pending_plan)
        self.assertIsNone(self.assistant.pending_action)

    def test_plan_can_pause_for_confirmation_twice(self):
        steps = [
            self._step("first"),
            self._step("second"),
            self._step("third"),
        ]

        self.assistant.agent.execute.side_effect = [
            {"status": "success"},
            self._confirmation("second", "token-2"),
            {"status": "success"},
            self._confirmation("third", "token-3"),
            {"status": "success"},
        ]
        self.assistant._summarize_results = Mock(
            return_value="All steps completed."
        )

        self.assistant._execute_plan_steps(
            steps, 0, [], "complete my task"
        )
        self.assistant._handle_confirmation("yes")

        self.assertIsNotNone(self.assistant.pending_plan)
        self.assertEqual(
            self.assistant.pending_plan["next_index"], 3
        )

        response = self.assistant._handle_confirmation("yes")

        self.assertEqual(response, "All steps completed.")
        self.assertEqual(
            [call.args[0] for call in
             self.assistant.agent.execute.call_args_list],
            ["first", "second", "second", "third", "third"],
        )
        self.assertIsNone(self.assistant.pending_plan)

    def test_failed_confirmation_stops_plan(self):
        steps = [
            self._step("first"),
            self._step("second"),
            self._step("third"),
        ]

        self.assistant.agent.execute.side_effect = [
            {"status": "success"},
            self._confirmation("second", "token-4"),
            {"status": "authorization_failed", "reason": "Denied."},
        ]

        self.assistant._execute_plan_steps(
            steps, 0, [], "complete my task"
        )
        del self.assistant._format_result
        response = self.assistant._handle_confirmation("yes")

        self.assertIsNone(self.assistant.pending_plan)
        self.assertIn("Denied", response)
        self.assertEqual(
            self.assistant.agent.execute.call_count, 3
        )

    def test_cancelling_plan_prevents_remaining_steps(self):
        self.assistant.pending_action = {
            "tool": "second",
            "parameters": {},
            "confirmation_token": "token-5",
        }
        self.assistant.pending_plan = {
            "steps": [
                self._step("second"),
                self._step("third"),
            ],
            "next_index": 1,
            "results": [],
            "pending_step": {
                "action": "second",
                "description": "second",
            },
            "original_input": "complete my task",
        }

        response = self.assistant._handle_confirmation("no")

        self.assertIn("cancelled", response.lower())
        self.assertIsNone(self.assistant.pending_plan)
        self.assertIsNone(self.assistant.pending_action)
        self.assistant.agent.execute.assert_not_called()
        self.assistant.agent.cancel_confirmation.assert_called_once_with(
            "token-5"
        )

    def test_confirmation_exception_stops_plan(self):
        steps = [
            self._step("first"),
            self._step("second"),
            self._step("third"),
        ]

        self.assistant.agent.execute.side_effect = [
            self._confirmation("second", "token-6"),
            RuntimeError("Execution error"),
        ]

        self.assistant._execute_plan_steps(
            steps, 0, [], "complete my task"
        )
        response = self.assistant._handle_confirmation("yes")

        self.assertIn("Execution error", response)
        self.assertIsNone(self.assistant.pending_plan)
        self.assertEqual(
            self.assistant.agent.execute.call_count, 2
        )

    def test_plan_step_smuggling_confirmation_token_is_rejected_in_assistant(self):
        from types import SimpleNamespace

        smuggled_step = SimpleNamespace(
            action="write_file",
            parameters={
                "path": "a.txt",
                "content": "b",
                "confirmation_token": "smuggled-token",
            },
            description="write file",
        )
        later_step = self._step("third")

        response = self.assistant._execute_plan_steps(
            [smuggled_step, later_step], 0, [], "write file"
        )

        self.assertIn("authorize", response.lower())
        self.assertIsNone(self.assistant.pending_plan)
        self.assistant.agent.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
