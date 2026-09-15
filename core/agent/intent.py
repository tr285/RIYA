from dataclasses import dataclass, field
from enum import Enum


class IntentType(str, Enum):
    CHAT = "chat"

    OPEN_APPLICATION = "open_application"
    CLOSE_APPLICATION = "close_application"

    CREATE_FOLDER = "create_folder"
    LIST_FILES = "list_files"

    SCREENSHOT = "screenshot"
    CURRENT_TIME = "current_time"

    EXIT = "exit"


@dataclass
class Intent:
    type: IntentType
    parameters: dict = field(default_factory=dict)


class IntentDetector:
    """
    Deterministic detector for simple, unambiguous commands.

    Natural-language commands that may contain multiple actions
    are deliberately routed to CHAT so RIYA's AI planner can
    decompose them into multiple tool actions.
    """

    # =========================================================
    # EXIT COMMANDS
    # =========================================================

    EXIT_COMMANDS = {
        "exit",
        "quit",
        "bye",
        "goodbye",
        "close riya",
        "stop riya",
    }

    # =========================================================
    # SCREENSHOT COMMANDS
    # =========================================================

    SCREENSHOT_COMMANDS = {
        "screenshot",
        "take screenshot",
        "take a screenshot",
        "capture screen",
        "capture the screen",
        "capture my screen",
        "take a screen capture",
        "capture a screenshot",
    }

    # =========================================================
    # CURRENT TIME COMMANDS
    # =========================================================

    CURRENT_TIME_COMMANDS = {
        "time",
        "current time",
        "what time is it",
        "what is the time",
        "tell me the time",
        "what's the time",
        "whats the time",
        "can you tell me the time",
    }

    # =========================================================
    # APPLICATION VERBS
    # =========================================================

    OPEN_VERBS = (
        "open ",
        "launch ",
        "start ",
        "run ",
    )

    CLOSE_VERBS = (
        "close ",
        "quit ",
        "exit ",
        "stop ",
    )

    # =========================================================
    # FOLDER COMMANDS
    # =========================================================

    CREATE_FOLDER_PREFIXES = (
        "create a folder called ",
        "create a folder named ",
        "create folder called ",
        "create folder named ",
        "make a folder called ",
        "make a folder named ",
        "make folder called ",
        "make folder named ",
    )

    # =========================================================
    # FILE LISTING COMMANDS
    # =========================================================

    LIST_FILES_PREFIXES = (
        "show files in ",
        "list files in ",
        "show me files in ",
        "list files from ",
        "show the files in ",
        "display files in ",
        "display the files in ",
    )

    # =========================================================
    # MAIN DETECTOR
    # =========================================================

    def detect(
        self,
        text: str,
    ) -> Intent:

        text = text.strip()

        if not text:
            return Intent(
                IntentType.CHAT
            )

        normalized = self._normalize(
            text
        )

        # -----------------------------------------------------
        # Exit
        # -----------------------------------------------------

        if normalized in self.EXIT_COMMANDS:

            return Intent(
                IntentType.EXIT
            )

        # -----------------------------------------------------
        # Multi-action request
        #
        # These must go to the AI planner.
        # -----------------------------------------------------

        if self._is_multi_action_request(
            normalized
        ):

            return Intent(
                IntentType.CHAT
            )

        # -----------------------------------------------------
        # Screenshot
        # -----------------------------------------------------

        if normalized in self.SCREENSHOT_COMMANDS:

            return Intent(
                IntentType.SCREENSHOT
            )

        # -----------------------------------------------------
        # Current time
        # -----------------------------------------------------

        if normalized in self.CURRENT_TIME_COMMANDS:

            return Intent(
                IntentType.CURRENT_TIME
            )

        # -----------------------------------------------------
        # Open application
        # -----------------------------------------------------

        app_name = self._extract_application_name(
            text,
            normalized,
            self.OPEN_VERBS,
        )

        if app_name:

            return Intent(
                IntentType.OPEN_APPLICATION,
                {
                    "app_name": app_name,
                },
            )

        # -----------------------------------------------------
        # Close application
        # -----------------------------------------------------

        app_name = self._extract_application_name(
            text,
            normalized,
            self.CLOSE_VERBS,
        )

        if app_name:

            return Intent(
                IntentType.CLOSE_APPLICATION,
                {
                    "app_name": app_name,
                },
            )

        # -----------------------------------------------------
        # Create folder
        # -----------------------------------------------------

        for prefix in self.CREATE_FOLDER_PREFIXES:

            if normalized.startswith(prefix):

                folder_name = text[
                    len(prefix):
                ].strip()

                if folder_name:

                    return Intent(
                        IntentType.CREATE_FOLDER,
                        {
                            "path": folder_name,
                        },
                    )

        # -----------------------------------------------------
        # List files
        # -----------------------------------------------------

        for prefix in self.LIST_FILES_PREFIXES:

            if normalized.startswith(prefix):

                path = text[
                    len(prefix):
                ].strip()

                if path:

                    return Intent(
                        IntentType.LIST_FILES,
                        {
                            "path": self._normalize_path_alias(
                                path
                            ),
                        },
                    )

        # -----------------------------------------------------
        # No deterministic command found
        # -----------------------------------------------------

        return Intent(
            IntentType.CHAT
        )

    # =========================================================
    # NORMALIZATION
    # =========================================================

    def _normalize(
        self,
        text: str,
    ) -> str:

        normalized = text.lower().strip()

        # Remove common punctuation at the end.
        normalized = normalized.rstrip(
            "!?.,"
        )

        # Collapse repeated whitespace.
        normalized = " ".join(
            normalized.split()
        )

        return normalized

    # =========================================================
    # APPLICATION NAME EXTRACTION
    # =========================================================

    def _extract_application_name(
        self,
        original_text: str,
        normalized_text: str,
        verbs,
    ) -> str | None:

        for verb in verbs:

            if not normalized_text.startswith(
                verb
            ):
                continue

            # Use the original text so application names
            # retain their original capitalization.

            app_name = original_text[
                len(verb):
            ].strip()

            app_name = app_name.rstrip(
                "!?.,"
            ).strip()

            if not app_name:
                return None

            # -------------------------------------------------
            # Natural-language suffixes
            #
            # Example:
            # "Open Safari for me"
            # → Safari
            # -------------------------------------------------

            suffixes = (
                " for me",
                " please",
                " for me please",
            )

            lowered_name = app_name.lower()

            for suffix in suffixes:

                if lowered_name.endswith(
                    suffix
                ):

                    app_name = app_name[
                        : -len(suffix)
                    ].strip()

                    break

            if app_name:
                return app_name

            return None

        return None

    # =========================================================
    # MULTI-ACTION DETECTION
    # =========================================================

    def _is_multi_action_request(
        self,
        normalized: str,
    ) -> bool:

        # -----------------------------------------------------
        # Explicit action separators
        # -----------------------------------------------------

        markers = (
            " and ",
            " then ",
            " after that ",
            " followed by ",
            " next ",
        )

        if any(
            marker in normalized
            for marker in markers
        ):

            return True

        # -----------------------------------------------------
        # Comma-separated commands
        #
        # Example:
        # "Open Safari, take a screenshot"
        # -----------------------------------------------------

        if "," in normalized:

            command_words = (
                "open ",
                "launch ",
                "start ",
                "close ",
                "quit ",
                "take ",
                "capture ",
                "set ",
                "mute",
                "unmute",
                "create ",
                "make ",
                "show ",
                "list ",
            )

            parts = [
                part.strip()
                for part in normalized.split(",")
                if part.strip()
            ]

            if len(parts) >= 2:

                command_like_parts = 0

                for part in parts:

                    if part.startswith(
                        command_words
                    ):

                        command_like_parts += 1

                if command_like_parts >= 2:

                    return True

        return False

    # =========================================================
    # PATH ALIASES
    # =========================================================

    def _normalize_path_alias(
        self,
        path: str,
    ) -> str:

        aliases = {
            "desktop": "~/Desktop",
            "my desktop": "~/Desktop",

            "documents": "~/Documents",
            "my documents": "~/Documents",

            "downloads": "~/Downloads",
            "my downloads": "~/Downloads",

            "home": "~",
            "home folder": "~",
            "home directory": "~",
        }

        normalized = self._normalize(
            path
        )

        return aliases.get(
            normalized,
            path,
        )