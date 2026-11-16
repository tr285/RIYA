import unittest
from unittest.mock import patch, Mock

from core.memory.service import MemoryService


class TestMemoryService(unittest.TestCase):

    def setUp(self):
        self.service = MemoryService()

    @patch("core.memory.service.recall_memory")
    def test_exact_memory_recall(self, mock_recall):
        mock_recall.return_value = "Tukaram"

        intent = self.service.detect("What is my name?")
        result = self.service.execute_safe(intent)

        self.assertEqual(result, "Tukaram")
        mock_recall.assert_called_once()

    @patch("core.memory.service.recall_memory")
    def test_recall_fallback_search(self, mock_recall):
        mock_recall.return_value = (
            "I don't have a memory for this key."
        )
        self.service.manager.search = Mock(
            return_value=[
                {"value": "Tukaram"}
            ]
        )

        intent = self.service.detect("What is my name?")
        result = self.service.execute_safe(intent)

        self.assertEqual(result, "Tukaram")
        self.service.manager.search.assert_called_once()

    @patch("core.memory.service.list_memories")
    def test_list_memories(self, mock_list):
        mock_list.return_value = "personal: my_name = Tukaram"

        intent = self.service.detect("List my memories")
        result = self.service.execute_safe(intent)

        self.assertEqual(
            result,
            "personal: my_name = Tukaram"
        )
        mock_list.assert_called_once()


if __name__ == "__main__":
    unittest.main()
