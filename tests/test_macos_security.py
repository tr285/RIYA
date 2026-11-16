import subprocess
import unittest
from unittest.mock import patch

from core.agent.tool_executor import ToolExecutor
from core.agent.tool_registry import ToolRegistry
from core.security.manager import SecurityManager
from core.tools.system import macos
from core.voice.tts import LocalTTS


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["/usr/bin/osascript"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class TestMacOSSecurity(unittest.TestCase):

    def test_validate_app_name_rejects_applescript_injection_and_special_chars(self):
        payloads = [
            "",
            "   ",
            None,
            123,
            'Safari" to quit\ndo shell script "id"\n--',
            'Safari"; do shell script "open -a Calculator"; --',
            "Safari' & do shell script 'id'",
            "Safari`id`",
            "Safari$(id)",
            "Safari|whoami",
            "Safari;id",
            "Safari\nquit",
            "Safari\x00",
            "-e",
            "--args",
            "../Evil.app",
            "/Applications/Safari.app",
            "App\\Name",
            "A" * 129,
        ]

        for payload in payloads:
            with self.subTest(payload=payload):
                with patch("core.tools.system.macos.subprocess.run") as mock_run:
                    with self.assertRaises(ValueError):
                        macos.close_application(payload)
                    with self.assertRaises(ValueError):
                        macos.open_application(payload)
                    mock_run.assert_not_called()

    def test_close_application_uses_on_run_argv_without_interpolation(self):
        with patch(
            "core.tools.system.macos.subprocess.run",
            return_value=_completed(0, stdout="CLOSED\n"),
        ) as mock_run:
            result = macos.close_application("Google Chrome")

        self.assertEqual(result, "Closed Google Chrome.")
        mock_run.assert_called_once()
        cmd = mock_run.call_args.args[0]
        kwargs = mock_run.call_args.kwargs

        self.assertEqual(cmd[0], "/usr/bin/osascript")
        self.assertFalse(kwargs["shell"])
        self.assertGreater(kwargs["timeout"], 0)
        self.assertIn("on run argv", cmd)
        self.assertIn("--", cmd)
        separator_index = cmd.index("--")
        self.assertEqual(cmd[separator_index + 1:], ["Google Chrome"])

        # App name must never be interpolated into any -e script argument.
        script_parts = [
            cmd[i + 1]
            for i in range(separator_index)
            if cmd[i] == "-e"
        ]
        self.assertFalse(any("Google Chrome" in part for part in script_parts))

    def test_run_osascript_keeps_quotes_and_special_chars_in_argv_only(self):
        payload = 'Test "Quote" & $pecial; `cmd`'
        with patch(
            "core.tools.system.macos.subprocess.run",
            return_value=_completed(0, stdout="ok\n"),
        ) as mock_run:
            macos._run_osascript(
                ["on run argv", "return item 1 of argv", "end run"],
                [payload],
            )

        cmd = mock_run.call_args.args[0]
        separator_index = cmd.index("--")
        self.assertEqual(cmd[separator_index + 1:], [payload])
        script_parts = [
            cmd[i + 1]
            for i in range(separator_index)
            if cmd[i] == "-e"
        ]
        self.assertFalse(any(payload in part for part in script_parts))

    def test_close_application_handles_missing_permission_timeout_and_still_running(self):
        with patch(
            "core.tools.system.macos.subprocess.run",
            return_value=_completed(0, stdout="NOT_RUNNING\n"),
        ):
            res = macos.close_application("Notes")
            self.assertNotIn("Closed", res)
            self.assertIn("not running", res.lower())

        with patch(
            "core.tools.system.macos.subprocess.run",
            return_value=_completed(0, stdout="STILL_RUNNING\n"),
        ):
            res = macos.close_application("Notes")
            self.assertNotIn("Closed", res)
            self.assertIn("still running", res.lower())

        with patch(
            "core.tools.system.macos.subprocess.run",
            return_value=_completed(
                1,
                stderr="execution error: Not authorized to send Apple events to Notes. (-1743)",
            ),
        ):
            res = macos.close_application("Notes")
            self.assertNotIn("Closed", res)
            self.assertIn("permission denied", res.lower())

        with patch(
            "core.tools.system.macos.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="osascript", timeout=10),
        ):
            res = macos.close_application("Notes")
            self.assertNotIn("Closed", res)
            self.assertIn("timed out", res.lower())

    def test_open_application_handles_errors_and_timeouts_safely(self):
        with patch(
            "core.tools.system.macos.subprocess.run",
            return_value=_completed(0),
        ) as mock_run:
            self.assertEqual(macos.open_application("Safari"), "Opened Safari.")
            self.assertEqual(mock_run.call_args.args[0], ["/usr/bin/open", "-a", "Safari"])
            self.assertFalse(mock_run.call_args.kwargs["shell"])
            self.assertGreater(mock_run.call_args.kwargs["timeout"], 0)

        with patch(
            "core.tools.system.macos.subprocess.run",
            return_value=_completed(1, stderr="Unable to find application named 'MissingApp'"),
        ):
            res = macos.open_application("MissingApp")
            self.assertNotIn("Opened", res)

        with patch(
            "core.tools.system.macos.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="open", timeout=10),
        ):
            res = macos.open_application("Safari")
            self.assertIn("timed out", res.lower())

    def test_set_volume_rejects_non_integers_and_uses_on_run_argv(self):
        for bad_level in ("50", '50\ndo shell script "id"', True, False, 50.5, None):
            with patch("core.tools.system.macos.subprocess.run") as mock_run:
                with self.assertRaises(ValueError):
                    macos.set_volume(bad_level)
                mock_run.assert_not_called()

        with patch(
            "core.tools.system.macos.subprocess.run",
            return_value=_completed(0),
        ) as mock_run:
            res = macos.set_volume(150)

        self.assertEqual(res, "Volume set to 100%.")
        cmd = mock_run.call_args.args[0]
        self.assertEqual(cmd[0], "/usr/bin/osascript")
        self.assertIn("on run argv", cmd)
        self.assertEqual(cmd[cmd.index("--") + 1:], ["100"])
        self.assertFalse(mock_run.call_args.kwargs["shell"])

        with patch(
            "core.tools.system.macos.subprocess.run",
            return_value=_completed(1, stderr="error"),
        ):
            res = macos.set_volume(40)
            self.assertNotIn("Volume set", res)

    def test_mute_unmute_lock_battery_sysinfo_use_safe_subprocess_settings(self):
        with patch(
            "core.tools.system.macos.subprocess.run",
            return_value=_completed(1, stderr="Not authorized (-1743)"),
        ):
            self.assertIn("permission denied", macos.mute_volume().lower())
            self.assertIn("permission denied", macos.unmute_volume().lower())
            self.assertIn("permission denied", macos.lock_screen().lower())

        with patch(
            "core.tools.system.macos.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="pmset", timeout=5),
        ):
            self.assertIn("timed out", macos.battery_status().lower())
            self.assertIn("timed out", macos.system_information().lower())

    def test_run_executable_rejects_unallowlisted_and_relative_binaries(self):
        with patch("core.tools.system.macos.subprocess.run") as mock_run:
            with self.assertRaises(ValueError):
                macos._run_executable("osascript", ["-e", "beep"])
            with self.assertRaises(ValueError):
                macos._run_executable("/bin/sh", ["-c", "id"])
            mock_run.assert_not_called()

    def test_take_screenshot_refuses_symlink_target(self):
        with patch("core.tools.system.macos.Path.is_symlink", return_value=True):
            with patch("core.tools.system.macos.subprocess.run") as mock_run:
                res = macos.take_screenshot()
                self.assertIn("couldn't take the screenshot", res.lower())
                mock_run.assert_not_called()

    def test_macos_authorization_policy_classification(self):
        security = SecurityManager()

        for safe_tool in ("current_time", "battery_status", "system_information"):
            decision = security.check(safe_tool)
            self.assertTrue(decision.allowed)
            self.assertFalse(decision.requires_confirmation)

        for protected_tool in (
            "open_application",
            "close_application",
            "take_screenshot",
            "set_volume",
            "mute_volume",
            "unmute_volume",
            "lock_screen",
        ):
            decision = security.check(protected_tool)
            self.assertTrue(decision.allowed, f"{protected_tool} should be allowed with confirmation")
            self.assertTrue(decision.requires_confirmation, f"{protected_tool} must require confirmation")

        unknown = security.check("run_shell_command")
        self.assertFalse(unknown.allowed)
        self.assertFalse(unknown.requires_confirmation)

    def test_close_application_executor_requires_and_binds_confirmation(self):
        registry = ToolRegistry()
        registry.register(
            name="close_application",
            description="Close a macOS application.",
            function=macos.close_application,
            parameters={"app_name"},
        )
        executor = ToolExecutor(registry)

        with patch("core.tools.system.macos.subprocess.run") as mock_run:
            pending = executor.execute("close_application", app_name="Notes")
            self.assertEqual(pending["status"], "confirmation_required")
            mock_run.assert_not_called()

            token = pending["confirmation_token"]

            # Token bound to Notes must not authorize closing Finder.
            mismatch = executor.execute(
                "close_application",
                confirmation_token=token,
                app_name="Finder",
            )
            self.assertEqual(mismatch["status"], "authorization_failed")
            mock_run.assert_not_called()

        # Re-issue for Notes and execute with mocked osascript + pgrep verification.
        pending = executor.execute("close_application", app_name="Notes")
        token = pending["confirmation_token"]

        with patch(
            "subprocess.run",
            side_effect=[
                _completed(0, stdout="CLOSED\n"),
                _completed(1),
            ],
        ) as mock_all:
            result = executor.execute(
                "close_application",
                confirmation_token=token,
                app_name="Notes",
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(mock_all.call_count, 2)
        self.assertEqual(mock_all.call_args_list[0].args[0][0], "/usr/bin/osascript")
        self.assertEqual(
            mock_all.call_args_list[1].args[0],
            ["/usr/bin/pgrep", "-x", "--", "Notes"],
        )

    def test_close_application_failed_or_still_running_never_reports_success(self):
        registry = ToolRegistry()
        registry.register(
            name="close_application",
            description="Close a macOS application.",
            function=macos.close_application,
            parameters={"app_name"},
        )
        executor = ToolExecutor(registry)

        pending = executor.execute("close_application", app_name="Notes")
        token = pending["confirmation_token"]

        with patch(
            "subprocess.run",
            return_value=_completed(0, stdout="STILL_RUNNING\n"),
        ):
            result = executor.execute(
                "close_application",
                confirmation_token=token,
                app_name="Notes",
            )

        self.assertEqual(result["status"], "verification_failed")

    def test_local_tts_rejects_bad_voice_and_passes_text_via_stdin(self):
        with self.assertRaises(ValueError):
            LocalTTS(voice="-f /etc/passwd")

        tts = LocalTTS(voice="Samantha")
        with patch("core.voice.tts.subprocess.run") as mock_run:
            tts.speak("-f /etc/passwd")

        cmd = mock_run.call_args.args[0]
        kwargs = mock_run.call_args.kwargs
        self.assertEqual(cmd, ["/usr/bin/say", "-v", "Samantha", "-f", "-"])
        self.assertEqual(kwargs["input"], "-f /etc/passwd")
        self.assertFalse(kwargs["shell"])
        self.assertGreater(kwargs["timeout"], 0)

    def test_verify_screenshot_rejects_failure_message_even_if_old_file_exists(self):
        registry = ToolRegistry()
        executor = ToolExecutor(registry)

        verification = executor.verify(
            "take_screenshot",
            {},
            "I couldn't take the screenshot.",
        )
        self.assertFalse(verification["success"])

    def test_verify_application_closed_rejects_pgrep_error_exit_code(self):
        registry = ToolRegistry()
        executor = ToolExecutor(registry)

        with patch(
            "subprocess.run",
            return_value=_completed(2, stderr="pgrep error"),
        ):
            verification = executor._verify_application_closed("Notes")

        self.assertFalse(verification["success"])

    def test_close_application_failure_stops_remaining_plan_steps(self):
        from core.agent.planner import Plan, PlanStep
        from core.security.manager import SecurityDecision

        registry = ToolRegistry()
        registry.register(
            name="close_application",
            description="Close app",
            function=macos.close_application,
            parameters={"app_name"},
        )
        later_called = []
        registry.register(
            name="current_time",
            description="Time",
            function=lambda: later_called.append(True) or "12:00 PM",
            parameters=set(),
        )
        executor = ToolExecutor(registry)

        plan = Plan(
            goal="Close Notes then check time",
            steps=[
                PlanStep(action="close_application", parameters={"app_name": "Notes"}),
                PlanStep(action="current_time", parameters={}),
            ],
        )

        with patch.object(
            executor.security,
            "check",
            return_value=SecurityDecision(allowed=True, requires_confirmation=False),
        ), patch(
            "subprocess.run",
            return_value=_completed(1, stderr="Not authorized (-1743)"),
        ):
            results = executor.execute_plan(plan)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["result"]["status"], "verification_failed")
        self.assertEqual(later_called, [])


if __name__ == "__main__":
    unittest.main()
