import tempfile
import unittest
from pathlib import Path

from core.memory.database import MemoryDatabase
from core.memory.manager import MemoryManager


class TestMemoryDatabase(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = MemoryDatabase(
            Path(self.temp_dir.name) / "test.db"
        )
        self.memory = MemoryManager(self.database)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_and_recall(self):
        self.memory.remember("personal", "my_name", "Tukaram")
        self.assertEqual(
            self.memory.recall("personal", "my_name"),
            "Tukaram"
        )

    def test_update_existing_memory(self):
        self.memory.remember("personal", "my_name", "Alex")
        self.memory.remember("personal", "my_name", "Tukaram")
        self.assertEqual(
            self.memory.recall("personal", "my_name"),
            "Tukaram"
        )

    def test_missing_memory_returns_none(self):
        self.assertIsNone(
            self.memory.recall("personal", "unknown")
        )

    def test_memory_persists_after_reopening_database(self):
        self.memory.remember("personal", "my_name", "Tukaram")

        reopened_database = MemoryDatabase(
            self.database.database_path
        )
        reopened_memory = MemoryManager(reopened_database)

        self.assertEqual(
            reopened_memory.recall("personal", "my_name"),
            "Tukaram"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
