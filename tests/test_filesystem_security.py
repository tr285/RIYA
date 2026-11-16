import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import settings
from core.tools.filesystem.files import (
    FilesystemAccessError,
    create_folder,
    find_files,
    list_files,
    read_file,
    write_file,
)


class TestFilesystemSecurity(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.allowed = self.root / "allowed"
        self.outside = self.root / "outside"
        self.allowed.mkdir()
        self.outside.mkdir()

        self.allowed_roots = patch.object(
            settings,
            "FILESYSTEM_ALLOWED_ROOTS",
            (self.allowed,),
        )
        self.allowed_roots.start()

    def tearDown(self):
        self.allowed_roots.stop()
        self.temp_dir.cleanup()

    def test_operations_succeed_inside_allowed_root(self):
        folder = self.allowed / "notes"
        file_path = folder / "hello.txt"

        self.assertIn("Folder created", create_folder(str(folder)))
        self.assertIn("File written", write_file(str(file_path), "hello"))
        self.assertEqual(read_file(str(file_path)), "hello")
        self.assertIn("[FILE] hello.txt", list_files(str(folder)))
        self.assertIn(str(file_path), find_files("hello", str(self.allowed)))

    def test_operations_outside_allowed_roots_are_denied_without_writes(self):
        blocked_folder = self.outside / "blocked"
        blocked_file = self.outside / "blocked.txt"

        with self.assertRaises(FilesystemAccessError):
            list_files(str(self.outside))

        with self.assertRaises(FilesystemAccessError):
            find_files("anything", str(self.outside))

        with self.assertRaises(FilesystemAccessError):
            read_file(str(blocked_file))

        with self.assertRaises(FilesystemAccessError):
            create_folder(str(blocked_folder))

        with self.assertRaises(FilesystemAccessError):
            write_file(str(blocked_file), "must not be written")

        self.assertFalse(blocked_folder.exists())
        self.assertFalse(blocked_file.exists())

    def test_symlink_escape_is_denied_and_not_returned_by_search(self):
        secret = self.outside / "secret.txt"
        secret.write_text("private", encoding="utf-8")
        escape = self.allowed / "escape"
        escape.symlink_to(self.outside, target_is_directory=True)

        with self.assertRaises(FilesystemAccessError):
            read_file(str(escape / "secret.txt"))

        with self.assertRaises(FilesystemAccessError):
            write_file(str(escape / "new.txt"), "must not be written")

        with self.assertRaises(FilesystemAccessError):
            create_folder(str(escape / "new-folder"))

        self.assertNotIn(
            str(escape / "secret.txt"),
            find_files("secret", str(self.allowed)),
        )
        self.assertNotIn("[DIR] escape", list_files(str(self.allowed)))
        self.assertEqual(secret.read_text(encoding="utf-8"), "private")
        self.assertFalse((self.outside / "new.txt").exists())
        self.assertFalse((self.outside / "new-folder").exists())

    def test_default_search_root_is_the_configured_project_root(self):
        project_file = self.allowed / "project-note.txt"
        project_file.write_text("safe", encoding="utf-8")

        with patch.object(settings, "PROJECT_ROOT", self.allowed):
            matches = find_files("project-note")

        self.assertIn(str(project_file), matches)



    def test_verify_file_content_matches_exact_content(self):
        from core.tools.filesystem.files import verify_file_content

        target = self.allowed / "verified.txt"
        target.write_text("hello\nRIYA", encoding="utf-8")

        self.assertTrue(
            verify_file_content(str(target), "hello\nRIYA")
        )

    def test_verify_file_content_rejects_mismatched_content(self):
        from core.tools.filesystem.files import verify_file_content

        target = self.allowed / "verified.txt"
        target.write_text("actual content", encoding="utf-8")

        self.assertFalse(
            verify_file_content(str(target), "different content")
        )

    def test_verify_file_content_rejects_symlink_escape(self):
        from core.tools.filesystem.files import verify_file_content

        secret = self.outside / "secret.txt"
        secret.write_text("private", encoding="utf-8")
        link = self.allowed / "secret-link"
        link.symlink_to(secret)

        self.assertFalse(
            verify_file_content(str(link), "private")
        )

    def test_verify_file_content_rejects_fifo_without_blocking(self):
        import os
        from core.tools.filesystem.files import verify_file_content

        fifo = self.allowed / "test-pipe"
        os.mkfifo(fifo)

        self.assertFalse(
            verify_file_content(str(fifo), "anything")
        )

if __name__ == "__main__":
    unittest.main()
