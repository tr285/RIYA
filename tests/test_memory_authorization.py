import unittest
from unittest.mock import Mock

from core.agent.tool_executor import ToolExecutor
from core.agent.tool_registry import ToolRegistry


class TestMemoryAuthorization(unittest.TestCase):

    def setUp(self):
        self.forget = Mock(
            return_value="Forgot 'my_name' from 'personal'."
        )

        registry = ToolRegistry()
        registry.register(
            name="forget_memory",
            description="Delete a stored memory.",
            function=self.forget,
            parameters={"category", "key"},
        )

        self.executor = ToolExecutor(registry)

    def test_forget_memory_requires_explicit_confirmation(self):
        result = self.executor.execute(
            "forget_memory",
            category="personal",
            key="my_name",
        )

        self.assertEqual(result["status"], "confirmation_required")
        self.forget.assert_not_called()

    def test_forget_memory_executes_only_after_confirmation(self):
        pending = self.executor.execute(
            "forget_memory",
            category="personal",
            key="my_name",
        )

        self.assertEqual(pending["status"], "confirmation_required")
        self.forget.assert_not_called()

        token = pending["confirmation_token"]

        result = self.executor.execute(
            "forget_memory",
            confirmation_token=token,
            category="personal",
            key="my_name",
        )

        self.assertEqual(result["status"], "success")
        self.forget.assert_called_once_with(
            category="personal",
            key="my_name",
        )

        replay = self.executor.execute(
            "forget_memory",
            confirmation_token=token,
            category="personal",
            key="my_name",
        )

        self.assertEqual(replay["status"], "authorization_failed")
        self.forget.assert_called_once()


if __name__ == "__main__":
    unittest.main()
