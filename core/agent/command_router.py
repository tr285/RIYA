from core.agent.intent import Intent, IntentType


class CommandRouter:
    """Convert user text into a RIYA intent."""

    def route(self, text: str) -> Intent:
        text = text.strip()
        lower = text.lower()

        # Exit
        if lower in {"exit", "quit", "stop", "goodbye"}:
            return Intent(IntentType.EXIT)

        # Open application
        if lower.startswith("open "):
            app_name = text[5:].strip()

            if app_name:
                return Intent(
                    IntentType.OPEN_APPLICATION,
                    {"app_name": app_name},
                )

        # Close application
        if lower.startswith("close "):
            app_name = text[6:].strip()

            if app_name:
                return Intent(
                    IntentType.CLOSE_APPLICATION,
                    {"app_name": app_name},
                )

        # Create folder
        if lower.startswith("create folder "):
            folder_name = text[14:].strip()

            if folder_name:
                return Intent(
                    IntentType.CREATE_FOLDER,
                    {"folder_name": folder_name},
                )

        # List files
        if lower in {
            "list files",
            "show files",
            "show my files",
        }:
            return Intent(IntentType.LIST_FILES)

        # Screenshot
        if lower in {
            "screenshot",
            "take screenshot",
            "take a screenshot",
            "capture screen",
        }:
            return Intent(IntentType.SCREENSHOT)

        # Current time
        if lower in {
            "time",
            "what time is it",
            "what is the time",
            "current time",
        }:
            return Intent(IntentType.CURRENT_TIME)

        # Everything else → AI conversation
        return Intent(IntentType.CHAT)