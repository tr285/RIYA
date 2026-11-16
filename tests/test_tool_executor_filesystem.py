import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.agent.planner import Plan, PlanStep
from core.agent.tool_executor import ToolExecutor
from core.agent.tool_registry import ToolRegistry
from core.config import settings
from core.security.manager import SecurityDecision
from core.tools.filesystem.files import write_file


class TestToolExecutorFilesystem(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

        self.allowed_roots_patch = patch.object(
            settings,
            "FILESYSTEM_ALLOWED_ROOTS",
            (self.root,),
        )
        self.allowed_roots_patch.start()

        self.registry = ToolRegistry()

        self.registry.register(
            name="write_file",
            description="Write content to a file",
            function=write_file,
            parameters={"path", "content"},
        )

        self.executor = ToolExecutor(self.registry)

    def tearDown(self):
        self.allowed_roots_patch.stop()
        self.temp_dir.cleanup()

    def test_write_requires_confirmation_before_execution(self):
        target = self.root / "test.txt"

        result = self.executor.execute(
            "write_file",
            path=str(target),
            content="Hello RIYA",
        )

        self.assertEqual(result["status"], "confirmation_required")
        self.assertFalse(target.exists())
        self.assertIn("confirmation_token", result)

    def test_confirmed_write_passes_exact_content_verification(self):
        target = self.root / "confirmed.txt"
        content = "Confirmed file content"

        confirmation = self.executor.execute(
            "write_file",
            path=str(target),
            content=content,
        )

        token = confirmation["confirmation_token"]

        result = self.executor.execute(
            "write_file",
            confirmation_token=token,
            path=str(target),
            content=content,
        )

        self.assertEqual(result["status"], "success")
        self.assertTrue(target.exists())
        self.assertEqual(target.read_text(), content)

        self.assertTrue(result.get("verification", {}).get("success", True))

    def test_confirmation_token_cannot_be_reused(self):
        target = self.root / "replay.txt"
        content = "One-time authorization"

        confirmation = self.executor.execute(
            "write_file",
            path=str(target),
            content=content,
        )

        token = confirmation["confirmation_token"]

        first_result = self.executor.execute(
            "write_file",
            confirmation_token=token,
            path=str(target),
            content=content,
        )

        self.assertEqual(first_result["status"], "success")

        second_result = self.executor.execute(
            "write_file",
            confirmation_token=token,
            path=str(target),
            content=content,
        )

        self.assertEqual(second_result["status"], "authorization_failed")

    def test_confirmation_token_cannot_authorize_another_tool(self):
        target = self.root / "another_tool.txt"
        content = "Must not be written"

        confirmation = self.executor.execute(
            "write_file",
            path=str(target),
            content=content,
        )

        token = confirmation["confirmation_token"]

        self.registry.register(
            name="another_write",
            description="Another file-writing tool",
            function=write_file,
            parameters={"path", "content"},
        )

        with patch.object(
            self.executor.security,
            "check",
            return_value=SecurityDecision(
                allowed=True,
                requires_confirmation=True,
                reason="Confirmation required.",
            ),
        ):
            result = self.executor.execute(
                "another_write",
                confirmation_token=token,
                path=str(target),
                content=content,
            )

        self.assertEqual(result["status"], "authorization_failed")
        self.assertFalse(target.exists())

    def test_confirmation_token_rejects_modified_content(self):
        target = self.root / "modified.txt"

        confirmation = self.executor.execute(
            "write_file",
            path=str(target),
            content="Original content",
        )

        token = confirmation["confirmation_token"]

        result = self.executor.execute(
            "write_file",
            confirmation_token=token,
            path=str(target),
            content="Modified content",
        )

        self.assertEqual(result["status"], "authorization_failed")
        self.assertFalse(target.exists())

    def test_cancelled_confirmation_cannot_write_file(self):
        target = self.root / "cancelled.txt"
        content = "This must never be written"

        confirmation = self.executor.execute(
            "write_file",
            path=str(target),
            content=content,
        )

        token = confirmation["confirmation_token"]

        self.executor.cancel_confirmation(token)

        result = self.executor.execute(
            "write_file",
            confirmation_token=token,
            path=str(target),
            content=content,
        )

        self.assertEqual(result["status"], "authorization_failed")
        self.assertFalse(target.exists())

    def test_expired_confirmation_token_cannot_write_file(self):
        current_time = [1000.0]
        executor = ToolExecutor(
            self.registry,
            confirmation_ttl_seconds=60,
            confirmation_clock=lambda: current_time[0],
        )
        target = self.root / "expired.txt"
        content = "Must not be written"

        confirmation = executor.execute(
            "write_file",
            path=str(target),
            content=content,
        )
        token = confirmation["confirmation_token"]

        current_time[0] += 61.0

        result = executor.execute(
            "write_file",
            confirmation_token=token,
            path=str(target),
            content=content,
        )

        self.assertEqual(result["status"], "authorization_failed")
        self.assertIn("expired", result.get("reason", "").lower())
        self.assertFalse(target.exists())

    def test_invalid_confirmation_token_cannot_write_file(self):
        target = self.root / "invalid_token.txt"
        content = "Must not be written"

        result = self.executor.execute(
            "write_file",
            confirmation_token="invalid-token-12345",
            path=str(target),
            content=content,
        )

        self.assertEqual(result["status"], "authorization_failed")
        self.assertFalse(target.exists())

    def test_failed_verification_stops_remaining_plan_steps(self):
        first_target = self.root / "incorrect.txt"
        second_target = self.root / "must_not_exist.txt"

        def incorrect_write(path, content):
            Path(path).write_text("Incorrect content")
            return "File written"

        self.registry.register(
            name="later_action",
            description="A later action",
            function=write_file,
            parameters={"path", "content"},
        )

        self.registry.get("write_file").function = incorrect_write

        plan = Plan(
            goal="Test plan execution stops on failed verification",
            steps=[
                PlanStep(
                    action="write_file",
                    parameters={
                        "path": str(first_target),
                        "content": "Expected content",
                    },
                    description="Write expected content",
                ),
                PlanStep(
                    action="later_action",
                    parameters={
                        "path": str(second_target),
                        "content": "Should not execute",
                    },
                    description="Subsequent step that should not run",
                ),
            ],
        )

        with patch.object(
            self.executor.security,
            "check",
            return_value=SecurityDecision(
                allowed=True,
                requires_confirmation=False,
                reason="Test execution",
            ),
        ):
            results = self.executor.execute_plan(plan)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["result"]["status"], "verification_failed")
        self.assertFalse(second_target.exists())

    def test_confirmation_token_rejects_modified_path(self):
        first = self.root / "first.txt"
        second = self.root / "second.txt"
        content = "Bound content"

        confirmation = self.executor.execute(
            "write_file",
            path=str(first),
            content=content,
        )
        token = confirmation["confirmation_token"]

        result = self.executor.execute(
            "write_file",
            confirmation_token=token,
            path=str(second),
            content=content,
        )

        self.assertEqual(result["status"], "authorization_failed")
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())

    def test_unknown_tool_is_denied_by_default(self):
        target = self.root / "unknown.txt"
        self.registry.register(
            name="unclassified_tool",
            description="Unclassified test tool",
            function=write_file,
            parameters={"path", "content"},
        )

        with self.assertRaises(PermissionError):
            self.executor.execute(
                "unclassified_tool",
                path=str(target),
                content="Should be blocked",
            )

        self.assertFalse(target.exists())

    def test_plan_step_cannot_smuggle_confirmation_token(self):
        target = self.root / "smuggled.txt"
        later = self.root / "later.txt"
        content = "Must not be written"

        confirmation = self.executor.execute(
            "write_file",
            path=str(target),
            content=content,
        )
        token = confirmation["confirmation_token"]

        plan = Plan(
            goal="Attempt confirmation token smuggling",
            steps=[
                PlanStep(
                    action="write_file",
                    parameters={
                        "path": str(target),
                        "content": content,
                        "confirmation_token": token,
                    },
                    description="Smuggled step",
                ),
                PlanStep(
                    action="write_file",
                    parameters={
                        "path": str(later),
                        "content": "Should not execute",
                    },
                    description="Later step",
                ),
            ],
        )

        results = self.executor.execute_plan(plan)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["result"]["status"], "authorization_failed")
        self.assertFalse(target.exists())
        self.assertFalse(later.exists())

    def test_filesystem_exception_fails_closed_and_stops_plan(self):
        outside_dir = tempfile.TemporaryDirectory()
        self.addCleanup(outside_dir.cleanup)
        outside_target = Path(outside_dir.name) / "escape.txt"
        later_target = self.root / "later.txt"

        plan = Plan(
            goal="Write outside allowed root",
            steps=[
                PlanStep(
                    action="write_file",
                    parameters={
                        "path": str(outside_target),
                        "content": "Blocked",
                    },
                ),
                PlanStep(
                    action="write_file",
                    parameters={
                        "path": str(later_target),
                        "content": "Should not execute",
                    },
                ),
            ],
        )

        with patch.object(
            self.executor.security,
            "check",
            return_value=SecurityDecision(
                allowed=True,
                requires_confirmation=False,
                reason="Bypass confirmation to test filesystem exception handling",
            ),
        ):
            results = self.executor.execute_plan(plan)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["result"]["status"], "failed")
        self.assertFalse(outside_target.exists())
        self.assertFalse(later_target.exists())

    def test_verify_path_exists_rejects_outside_root_and_symlink_escape(self):
        outside_dir = tempfile.TemporaryDirectory()
        self.addCleanup(outside_dir.cleanup)
        outside_folder = Path(outside_dir.name) / "secret_dir"
        outside_folder.mkdir()

        symlink_dir = self.root / "escape_dir"
        symlink_dir.symlink_to(outside_folder, target_is_directory=True)

        outside_check = self.executor.verify(
            "create_folder",
            {"path": str(outside_folder)},
            "Folder created",
        )
        symlink_check = self.executor.verify(
            "create_folder",
            {"path": str(symlink_dir)},
            "Folder created",
        )

        self.assertFalse(outside_check["success"])
        self.assertFalse(symlink_check["success"])

    def test_concurrent_execution_consumes_token_only_once(self):
        import threading

        target = self.root / "concurrent.txt"
        content = "Only one write allowed"
        write_calls = []
        original_write = write_file

        def counting_write(path, content):
            write_calls.append(path)
            return original_write(path, content)

        self.registry.get("write_file").function = counting_write

        confirmation = self.executor.execute(
            "write_file",
            path=str(target),
            content=content,
        )
        token = confirmation["confirmation_token"]

        threads_count = 6
        barrier = threading.Barrier(threads_count)
        statuses = []
        statuses_lock = threading.Lock()

        def worker():
            barrier.wait()
            res = self.executor.execute(
                "write_file",
                confirmation_token=token,
                path=str(target),
                content=content,
            )
            with statuses_lock:
                statuses.append(res["status"])

        threads = [threading.Thread(target=worker) for _ in range(threads_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(statuses.count("success"), 1)
        self.assertEqual(statuses.count("authorization_failed"), threads_count - 1)
        self.assertEqual(len(write_calls), 1)
        self.assertEqual(target.read_text(), content)


if __name__ == "__main__":
    unittest.main()
