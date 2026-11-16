import types
import unittest
from unittest.mock import Mock, patch

from core.agent.planner import AgentPlanner
from core.agent.tool_registry import ToolRegistry


class TestStartupResilience(unittest.TestCase):

    def setUp(self):
        # Importing the assistant normally constructs the legacy module-level
        # memory helper. Suppress its SQLite schema initialization so this
        # startup-resilience test never touches the user's database.
        from core.memory.database import MemoryDatabase

        with patch.object(MemoryDatabase, "_initialize"):
            from core.agent.assistant import RIYAAssistant

        self.assistant = RIYAAssistant.__new__(RIYAAssistant)
        self.assistant.agent = Mock()
        self.assistant.agent.tools = Mock()

    def test_missing_semantic_dependency_falls_back_without_startup_failure(self):
        with patch(
            "core.agent.assistant.importlib.import_module",
            side_effect=ModuleNotFoundError("No module named 'numpy'"),
        ):
            semantic = self.assistant._initialize_semantic_engine()

        self.assertIsNone(semantic)
        self.assertIn("numpy", self.assistant.semantic_unavailable_reason)

    def test_embedding_load_failure_falls_back_without_startup_failure(self):
        failing_module = types.SimpleNamespace(
            SemanticToolEngine=Mock(
                side_effect=RuntimeError("embedding model unavailable")
            )
        )

        with patch(
            "core.agent.assistant.importlib.import_module",
            return_value=failing_module,
        ):
            semantic = self.assistant._initialize_semantic_engine()

        self.assertIsNone(semantic)
        self.assertIn(
            "embedding model unavailable",
            self.assistant.semantic_unavailable_reason,
        )

    def test_unexpected_semantic_initialization_failure_falls_back(self):
        failing_module = types.SimpleNamespace(
            SemanticToolEngine=Mock(
                side_effect=OSError("cached embedding model is unavailable")
            )
        )

        with patch(
            "core.agent.assistant.importlib.import_module",
            return_value=failing_module,
        ):
            semantic = self.assistant._initialize_semantic_engine()

        self.assertIsNone(semantic)
        self.assertIn(
            "cached embedding model is unavailable",
            self.assistant.semantic_unavailable_reason,
        )

    def test_planner_without_semantic_engine_uses_complete_registry(self):
        registry = ToolRegistry()

        registry.register(
            name="current_time",
            description="Get the current local time.",
            function=lambda: "time",
            parameters=set(),
        )
        registry.register(
            name="read_file",
            description="Read a text file.",
            function=lambda path: path,
            parameters={"path"},
        )

        planner = AgentPlanner(
            ai_runtime=Mock(),
            tool_registry=registry,
            semantic_engine=None,
        )

        descriptions = planner._get_tool_descriptions("What time is it?")

        self.assertIn("current_time()", descriptions)
        self.assertIn("read_file(path)", descriptions)

    def test_planner_falls_back_to_complete_registry_after_retrieval_failure(self):
        registry = ToolRegistry()

        registry.register(
            name="current_time",
            description="Get the current local time.",
            function=lambda: "time",
            parameters=set(),
        )
        registry.register(
            name="read_file",
            description="Read a text file.",
            function=lambda path: path,
            parameters={"path"},
        )

        semantic = Mock()
        semantic.relevant_tool_descriptions.side_effect = RuntimeError(
            "embedding index unavailable"
        )
        planner = AgentPlanner(
            ai_runtime=Mock(),
            tool_registry=registry,
            semantic_engine=semantic,
        )

        descriptions = planner._get_tool_descriptions("What time is it?")

        self.assertIn("current_time()", descriptions)
        self.assertIn("read_file(path)", descriptions)


if __name__ == "__main__":
    unittest.main()
