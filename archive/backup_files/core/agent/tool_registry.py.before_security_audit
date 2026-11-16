from dataclasses import dataclass
from typing import Callable, Dict, Optional


@dataclass
class Tool:
    """A capability available to RIYA."""

    name: str
    description: str
    function: Callable
    parameters: set[str]


class ToolRegistry:
    """Registry of tools available to the RIYA agent."""

    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(
        self,
        name: str,
        description: str,
        function: Callable,
        parameters: Optional[set[str]] = None,
    ) -> None:
        """Register a tool and its accepted parameters."""

        if name in self._tools:
            raise ValueError(
                f"Tool already registered: {name}"
            )

        self._tools[name] = Tool(
            name=name,
            description=description,
            function=function,
            parameters=parameters or set(),
        )

    def get(self, name: str) -> Optional[Tool]:
        """Get a registered tool."""

        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        """Return all registered tools."""

        return list(self._tools.values())

    def has(self, name: str) -> bool:
        """Check whether a tool exists."""

        return name in self._tools

    def validate_parameters(
        self,
        name: str,
        parameters: dict,
    ) -> None:
        """Validate parameters before tool execution."""

        tool = self.get(name)

        if tool is None:
            raise ValueError(
                f"Unknown RIYA tool: {name}"
            )

        unexpected = set(parameters) - tool.parameters

        if unexpected:
            raise ValueError(
                f"Unexpected parameters for '{name}': "
                f"{sorted(unexpected)}. "
                f"Allowed: {sorted(tool.parameters)}"
            )