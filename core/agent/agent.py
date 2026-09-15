from core.agent.planner import Plan
from core.agent.tool_registry import ToolRegistry
from core.agent.tool_executor import ToolExecutor

from core.device.macos import MacOSPlatform

from core.tools.filesystem.files import (
    list_files,
    find_files,
    create_folder,
    read_file,
    write_file,
)

from core.tools.memory import (
    remember,
    recall_memory,
    list_memories,
    forget_memory,
)


class RiyaAgent:
    """
    RIYA's central agent.

    Responsibilities:

    - Register available tools
    - Provide tools to the planner
    - Execute tools through ToolExecutor
    - Keep security centralized
    - Support multi-step plans
    """

    def __init__(self):

        # =====================================================
        # CORE TOOL SYSTEM
        # =====================================================

        self.tools = ToolRegistry()

        self.executor = ToolExecutor(
            self.tools
        )

        # =====================================================
        # PLATFORM
        # =====================================================

        self.platform = MacOSPlatform()

        # =====================================================
        # REGISTER TOOLS
        # =====================================================

        self._register_platform_tools()

        self._register_filesystem_tools()

        self._register_memory_tools()

    # =========================================================
    # PLATFORM TOOLS
    # =========================================================

    def _register_platform_tools(self):

        self.tools.register(
            name="open_application",
            description=(
                "Open an application on the Mac."
            ),
            function=self.platform.open_application,
            parameters={
                "app_name",
            },
        )

        self.tools.register(
            name="close_application",
            description=(
                "Close an application on the Mac."
            ),
            function=self.platform.close_application,
            parameters={
                "app_name",
            },
        )

        self.tools.register(
            name="take_screenshot",
            description=(
                "Take a screenshot of the current screen."
            ),
            function=self.platform.take_screenshot,
            parameters=set(),
        )

        self.tools.register(
            name="current_time",
            description=(
                "Get the current local time."
            ),
            function=self.platform.current_time,
            parameters=set(),
        )

        self.tools.register(
            name="set_volume",
            description=(
                "Set the Mac system volume."
            ),
            function=self.platform.set_volume,
            parameters={
                "level",
            },
        )

        self.tools.register(
            name="mute_volume",
            description=(
                "Mute the Mac system volume."
            ),
            function=self.platform.mute_volume,
            parameters=set(),
        )

        self.tools.register(
            name="unmute_volume",
            description=(
                "Unmute the Mac system volume."
            ),
            function=self.platform.unmute_volume,
            parameters=set(),
        )

        self.tools.register(
            name="battery_status",
            description=(
                "Get the current Mac battery status."
            ),
            function=self.platform.battery_status,
            parameters=set(),
        )

        self.tools.register(
            name="system_information",
            description=(
                "Get system information about the Mac."
            ),
            function=self.platform.system_information,
            parameters=set(),
        )

        self.tools.register(
            name="lock_screen",
            description=(
                "Lock the Mac screen."
            ),
            function=self.platform.lock_screen,
            parameters=set(),
        )

    # =========================================================
    # FILESYSTEM TOOLS
    # =========================================================

    def _register_filesystem_tools(self):

        self.tools.register(
            name="list_files",
            description=(
                "List files and folders inside a directory."
            ),
            function=list_files,
            parameters={
                "path",
            },
        )

        self.tools.register(
            name="find_files",
            description=(
                "Find files or folders by name."
            ),
            function=find_files,
            parameters={
                "name",
                "path",
            },
        )

        self.tools.register(
            name="create_folder",
            description=(
                "Create a new folder."
            ),
            function=create_folder,
            parameters={
                "path",
            },
        )

        self.tools.register(
            name="read_file",
            description=(
                "Read the contents of a text file."
            ),
            function=read_file,
            parameters={
                "path",
            },
        )

        self.tools.register(
            name="write_file",
            description=(
                "Write text content to a file."
            ),
            function=write_file,
            parameters={
                "path",
                "content",
            },
        )

    # =========================================================
    # MEMORY TOOLS
    # =========================================================

    def _register_memory_tools(self):

        self.tools.register(
            name="remember",
            description=(
                "Store a user memory."
            ),
            function=remember,
            parameters={
                "category",
                "key",
                "value",
            },
        )

        self.tools.register(
            name="recall_memory",
            description=(
                "Recall a specific stored memory."
            ),
            function=recall_memory,
            parameters={
                "category",
                "key",
            },
        )

        self.tools.register(
            name="list_memories",
            description=(
                "List stored memories."
            ),
            function=list_memories,
            parameters={
                "category",
            },
        )

        self.tools.register(
            name="forget_memory",
            description=(
                "Delete a stored memory."
            ),
            function=forget_memory,
            parameters={
                "category",
                "key",
            },
        )

    # =========================================================
    # PLANNING
    # =========================================================

    def plan(
        self,
        goal: str,
    ) -> Plan:

        return Plan(
            goal=goal
        )

    def create_ai_plan(
        self,
        goal: str,
        ai_runtime,
    ):

        from core.agent.planner import (
            AgentPlanner,
        )

        planner = AgentPlanner(
            ai_runtime,
            self.tools,
        )

        return planner.create_plan(
            goal
        )

    # =========================================================
    # TOOL INFORMATION
    # =========================================================

    def available_tools(self):

        return self.tools.list_tools()

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
        Execute a single RIYA tool.

        All execution goes through ToolExecutor,
        including security and verification.
        """

        return self.executor.execute(
            tool_name,
            confirm=confirm,
            **parameters,
        )

    # =========================================================
    # MULTI-STEP PLAN EXECUTION
    # =========================================================

    def execute_plan(
        self,
        plan,
        confirm: bool = False,
    ):
        """
        Execute a complete multi-step RIYA plan.

        ToolExecutor handles:

        - security
        - confirmation
        - execution
        - verification
        - failure handling
        """

        return self.executor.execute_plan(
            plan,
            confirm=confirm,
        )

    # =========================================================
    # SIMPLE DECISION + EXECUTION
    # =========================================================

    def decide_and_execute(
        self,
        request: str,
        ai_runtime,
    ):
        """
        Use the AI decision engine for a request.

        Returns None when no tool is required.
        """

        from core.agent.tool_decision import (
            ToolDecisionEngine,
        )

        decision_engine = ToolDecisionEngine(
            ai_runtime,
            self.tools,
        )

        decision = decision_engine.decide(
            request
        )

        if decision.tool is None:
            return None

        return self.execute(
            decision.tool,
            **decision.parameters,
        )