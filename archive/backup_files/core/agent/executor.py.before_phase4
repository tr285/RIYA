from core.agent.intent import IntentType


class CommandExecutor:
    """
    Compatibility adapter for RIYA's command router.

    All real actions are delegated to RiyaAgent so that execution
    passes through the central tool registry and security layer.
    """

    def __init__(self, agent=None):
        self.agent = agent

    def set_agent(self, agent):
        self.agent = agent

    def execute(self, intent):
        if self.agent is None:
            raise RuntimeError(
                "CommandExecutor is not connected to RiyaAgent."
            )

        intent_type = intent.type

        # Some older Intent implementations store arguments
        # differently. Normalize them here.
        parameters = getattr(intent, "parameters", None)

        if parameters is None:
            parameters = {}

            # Compatibility with Intent objects that use
            # dedicated attributes instead of parameters.
            if hasattr(intent, "app_name"):
                parameters["app_name"] = intent.app_name

            if hasattr(intent, "path"):
                parameters["path"] = intent.path

            if hasattr(intent, "directory"):
                parameters["directory"] = intent.directory

        if intent_type == IntentType.OPEN_APPLICATION:
            app_name = parameters.get("app_name")

            if not app_name:
                raise ValueError(
                    "No application name was provided."
                )

            return self.agent.execute(
                "open_application",
                app_name=app_name,
            )

        if intent_type == IntentType.CLOSE_APPLICATION:
            app_name = parameters.get("app_name")

            if not app_name:
                raise ValueError(
                    "No application name was provided."
                )

            return self.agent.execute(
                "close_application",
                app_name=app_name,
            )

        if intent_type == IntentType.CREATE_FOLDER:
            path = parameters.get("path")

            if not path:
                raise ValueError(
                    "No folder path was provided."
                )

            return self.agent.execute(
                "create_folder",
                path=path,
            )

        if intent_type == IntentType.LIST_FILES:
            path = parameters.get(
                "path",
                parameters.get("directory", "."),
            )

            return self.agent.execute(
                "list_files",
                path=path,
            )

        if intent_type == IntentType.SCREENSHOT:
            return self.agent.execute(
                "take_screenshot"
            )

        if intent_type == IntentType.CURRENT_TIME:
            return self.agent.execute(
                "current_time"
            )

        if intent_type == IntentType.EXIT:
            return "Goodbye."

        if intent_type == IntentType.CHAT:
            return None

        raise ValueError(
            f"Unsupported RIYA intent: {intent_type}"
        )