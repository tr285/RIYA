from core.agent.tool_registry import ToolRegistry
from core.security.manager import SecurityManager


class ToolExecutor:
    """
    Central execution layer for RIYA.

    Responsibilities:
    - Validate tools
    - Validate parameters
    - Apply security rules
    - Handle confirmation
    - Execute tools
    - Verify important actions
    - Execute multi-step plans safely
    """

    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self.security = SecurityManager()

    # =========================================================
    # SINGLE TOOL EXECUTION
    # =========================================================

    def execute(
        self,
        tool_name: str,
        confirm: bool = False,
        **parameters,
    ):
        """
        Execute one RIYA tool.

        Returns a structured result.
        """

        # -----------------------------------------------------
        # 1. Find tool
        # -----------------------------------------------------

        tool = self.registry.get(tool_name)

        if tool is None:
            raise ValueError(
                f"RIYA does not have a tool named "
                f"'{tool_name}'."
            )

        # -----------------------------------------------------
        # 2. Validate parameters
        # -----------------------------------------------------

        self.registry.validate_parameters(
            tool_name,
            parameters,
        )

        # -----------------------------------------------------
        # 3. Security check
        # -----------------------------------------------------

        decision = self.security.check(
            tool_name
        )

        if not decision.allowed:
            raise PermissionError(
                f"RIYA blocked '{tool_name}': "
                f"{decision.reason}"
            )

        # -----------------------------------------------------
        # 4. Confirmation
        # -----------------------------------------------------

        if (
            decision.requires_confirmation
            and not confirm
        ):
            return {
                "status": "confirmation_required",
                "tool": tool_name,
                "parameters": parameters,
                "reason": decision.reason,
            }

        # -----------------------------------------------------
        # 5. Execute tool
        # -----------------------------------------------------

        try:
            result = tool.function(
                **parameters
            )

        except Exception as exc:
            return {
                "status": "failed",
                "tool": tool_name,
                "parameters": parameters,
                "error": str(exc),
            }

        # -----------------------------------------------------
        # 6. Verify execution
        # -----------------------------------------------------

        verification = self.verify(
            tool_name,
            parameters,
            result,
        )

        if not verification["success"]:
            return {
                "status": "verification_failed",
                "tool": tool_name,
                "parameters": parameters,
                "result": result,
                "reason": verification["reason"],
            }

        # -----------------------------------------------------
        # 7. Successful execution
        # -----------------------------------------------------

        return {
            "status": "success",
            "tool": tool_name,
            "parameters": parameters,
            "result": result,
            "verification": verification,
        }

    # =========================================================
    # MULTI-STEP PLAN EXECUTION
    # =========================================================

    def execute_plan(
        self,
        plan,
        confirm: bool = False,
    ):
        """
        Execute a RIYA multi-step plan.

        Execution stops when:
        - confirmation is required
        - a tool fails
        - verification fails
        """

        results = []

        for step in plan.steps:

            # -------------------------------------------------
            # Internal planning step
            # -------------------------------------------------

            if step.action == "understand":

                results.append({
                    "status": "success",
                    "action": "understand",
                    "description": step.description,
                    "result": "Task understood.",
                })

                continue

            # -------------------------------------------------
            # Execute step
            # -------------------------------------------------

            result = self.execute(
                step.action,
                confirm=confirm,
                **step.parameters,
            )

            step_result = {
                "action": step.action,
                "description": step.description,
                "result": result,
            }

            results.append(step_result)

            # -------------------------------------------------
            # Confirmation required
            # -------------------------------------------------

            if (
                isinstance(result, dict)
                and result.get("status")
                == "confirmation_required"
            ):
                break

            # -------------------------------------------------
            # Execution failure
            # -------------------------------------------------

            if (
                isinstance(result, dict)
                and result.get("status")
                in {
                    "failed",
                    "verification_failed",
                }
            ):
                break

        return results

    # =========================================================
    # VERIFICATION
    # =========================================================

    def verify(
        self,
        tool_name: str,
        parameters: dict,
        result,
    ) -> dict:
        """
        Verify that an important action actually happened.
        """

        # -----------------------------------------------------
        # Open application
        # -----------------------------------------------------

        if tool_name == "open_application":

            app_name = parameters.get(
                "app_name"
            )

            if not app_name:
                return {
                    "success": False,
                    "reason": (
                        "Application name was missing."
                    ),
                }

            return self._verify_application_running(
                app_name
            )

        # -----------------------------------------------------
        # Close application
        # -----------------------------------------------------

        if tool_name == "close_application":

            app_name = parameters.get(
                "app_name"
            )

            if not app_name:
                return {
                    "success": False,
                    "reason": (
                        "Application name was missing."
                    ),
                }

            return self._verify_application_closed(
                app_name
            )

        # -----------------------------------------------------
        # Screenshot
        # -----------------------------------------------------

        if tool_name == "take_screenshot":

            return self._verify_screenshot(
                result
            )

        # -----------------------------------------------------
        # Create folder
        # -----------------------------------------------------

        if tool_name == "create_folder":

            path = parameters.get(
                "path"
            )

            return self._verify_path_exists(
                path,
                expected_type="directory",
            )

        # -----------------------------------------------------
        # Write file
        # -----------------------------------------------------

        if tool_name == "write_file":

            path = parameters.get(
                "path"
            )

            return self._verify_path_exists(
                path,
                expected_type="file",
            )

        # -----------------------------------------------------
        # Delete file
        # -----------------------------------------------------

        if tool_name == "delete_file":

            path = parameters.get(
                "path"
            )

            return self._verify_path_missing(
                path
            )

        # -----------------------------------------------------
        # Delete folder
        # -----------------------------------------------------

        if tool_name == "delete_folder":

            path = parameters.get(
                "path"
            )

            return self._verify_path_missing(
                path
            )

        # -----------------------------------------------------
        # Other tools
        # -----------------------------------------------------

        if isinstance(result, dict):

            if result.get("status") == "failed":
                return {
                    "success": False,
                    "reason": result.get(
                        "error",
                        "Tool execution failed.",
                    ),
                }

        return {
            "success": True,
            "reason": "Tool completed successfully.",
        }

    # =========================================================
    # VERIFY APPLICATION RUNNING
    # =========================================================

    def _verify_application_running(
        self,
        app_name: str,
    ):
        import subprocess

        try:
            result = subprocess.run(
                [
                    "pgrep",
                    "-x",
                    app_name,
                ],
                capture_output=True,
                text=True,
                timeout=3,
            )

            if result.returncode == 0:
                return {
                    "success": True,
                    "reason": (
                        f"{app_name} is running."
                    ),
                }

            return {
                "success": False,
                "reason": (
                    f"{app_name} could not be "
                    "verified as running."
                ),
            }

        except Exception as exc:
            return {
                "success": False,
                "reason": (
                    "Application verification failed: "
                    f"{exc}"
                ),
            }

    # =========================================================
    # VERIFY APPLICATION CLOSED
    # =========================================================

    def _verify_application_closed(
        self,
        app_name: str,
    ):
        import subprocess

        try:
            result = subprocess.run(
                [
                    "pgrep",
                    "-x",
                    app_name,
                ],
                capture_output=True,
                text=True,
                timeout=3,
            )

            if result.returncode != 0:
                return {
                    "success": True,
                    "reason": (
                        f"{app_name} is no longer running."
                    ),
                }

            return {
                "success": False,
                "reason": (
                    f"{app_name} is still running."
                ),
            }

        except Exception as exc:
            return {
                "success": False,
                "reason": (
                    "Application verification failed: "
                    f"{exc}"
                ),
            }

    # =========================================================
    # VERIFY SCREENSHOT
    # =========================================================

    def _verify_screenshot(
        self,
        result,
    ):
        from pathlib import Path

        if not isinstance(result, str):
            return {
                "success": False,
                "reason": (
                    "Screenshot tool did not return "
                    "a file path."
                ),
            }

        possible_path = None

        # -----------------------------------------------------
        # Try paths contained in the tool result
        # -----------------------------------------------------

        for token in result.split():

            cleaned = token.strip(
                ".,:;()[]{}'\""
            )

            candidate = Path(
                cleaned
            ).expanduser()

            if (
                cleaned.startswith("/")
                and candidate.exists()
            ):
                possible_path = candidate
                break

        # -----------------------------------------------------
        # Search Desktop for recent screenshot
        # -----------------------------------------------------

        if possible_path is None:

            desktop = (
                Path.home()
                / "Desktop"
            )

            if desktop.exists():

                screenshots = sorted(
                    desktop.glob(
                        "*screenshot*.png"
                    ),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )

                if screenshots:
                    possible_path = (
                        screenshots[0]
                    )

        # -----------------------------------------------------
        # Could not verify
        # -----------------------------------------------------

        if possible_path is None:
            return {
                "success": False,
                "reason": (
                    "Screenshot was reported as created, "
                    "but RIYA could not verify the file."
                ),
            }

        # -----------------------------------------------------
        # File must exist
        # -----------------------------------------------------

        if not possible_path.is_file():
            return {
                "success": False,
                "reason": (
                    f"Screenshot file does not exist: "
                    f"{possible_path}"
                ),
            }

        # -----------------------------------------------------
        # File must not be empty
        # -----------------------------------------------------

        if possible_path.stat().st_size == 0:
            return {
                "success": False,
                "reason": (
                    f"Screenshot file is empty: "
                    f"{possible_path}"
                ),
            }

        return {
            "success": True,
            "reason": (
                f"Screenshot verified: "
                f"{possible_path}"
            ),
        }

    # =========================================================
    # VERIFY PATH EXISTS
    # =========================================================

    def _verify_path_exists(
        self,
        path: str | None,
        expected_type: str,
    ):
        from pathlib import Path

        if not path:
            return {
                "success": False,
                "reason": "Path was missing.",
            }

        resolved = (
            Path(path)
            .expanduser()
            .resolve()
        )

        if not resolved.exists():
            return {
                "success": False,
                "reason": (
                    f"Path was not created: "
                    f"{resolved}"
                ),
            }

        if expected_type == "directory":

            if not resolved.is_dir():
                return {
                    "success": False,
                    "reason": (
                        f"Expected a directory but "
                        f"found: {resolved}"
                    ),
                }

        if expected_type == "file":

            if not resolved.is_file():
                return {
                    "success": False,
                    "reason": (
                        f"Expected a file but "
                        f"found: {resolved}"
                    ),
                }

        return {
            "success": True,
            "reason": (
                f"Verified: {resolved}"
            ),
        }

    # =========================================================
    # VERIFY PATH IS MISSING
    # =========================================================

    def _verify_path_missing(
        self,
        path: str | None,
    ):
        from pathlib import Path

        if not path:
            return {
                "success": False,
                "reason": "Path was missing.",
            }

        resolved = (
            Path(path)
            .expanduser()
            .resolve()
        )

        if resolved.exists():
            return {
                "success": False,
                "reason": (
                    f"Path still exists: "
                    f"{resolved}"
                ),
            }

        return {
            "success": True,
            "reason": (
                f"Verified missing: "
                f"{resolved}"
            ),
        }