from dataclasses import dataclass


@dataclass
class ConversationMessage:
    role: str
    content: str


class ConversationContext:
    """
    Short-term conversation context.

    This data lives only for the current RIYA process/session.
    It is NOT written to the persistent memory database.
    """

    def __init__(self, max_messages: int = 12):
        if max_messages < 2:
            raise ValueError(
                "max_messages must be at least 2."
            )

        self.max_messages = max_messages
        self._messages: list[ConversationMessage] = []

    def add_user(self, content: str) -> None:
        self._add(
            role="user",
            content=content,
        )

    def add_assistant(self, content: str) -> None:
        self._add(
            role="assistant",
            content=content,
        )

    def _add(
        self,
        role: str,
        content: str,
    ) -> None:

        content = content.strip()

        if not content:
            return

        self._messages.append(
            ConversationMessage(
                role=role,
                content=content,
            )
        )

        self._trim()

    def _trim(self) -> None:
        if len(self._messages) <= self.max_messages:
            return

        self._messages = self._messages[
            -self.max_messages:
        ]

    def messages(self) -> list[ConversationMessage]:
        return list(self._messages)

    def clear(self) -> None:
        self._messages.clear()

    def __len__(self) -> int:
        return len(self._messages)

    def format_for_prompt(self) -> str:
        if not self._messages:
            return ""

        lines = []

        for message in self._messages:
            if message.role == "user":
                speaker = "User"
            else:
                speaker = "RIYA"

            lines.append(
                f"{speaker}: {message.content}"
            )

        return "\n".join(lines)