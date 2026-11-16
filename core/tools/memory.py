from __future__ import annotations

from typing import Any

from core.identity.context import get_current_execution_context
from core.memory.manager import MemoryManager

_shared_memory_manager: MemoryManager | None = None
_default_memory_manager: MemoryManager | None = None


def get_memory_manager(database: Any = None) -> MemoryManager:
    """Retrieve or lazily initialize the default shared MemoryManager instance."""
    global _default_memory_manager
    if _default_memory_manager is None or database is not None:
        _default_memory_manager = MemoryManager(database)
    return _default_memory_manager


def set_memory_manager(manager: MemoryManager | None) -> None:
    """Explicitly inject an isolated MemoryManager instance (for tests and isolated contexts)."""
    global _shared_memory_manager
    _shared_memory_manager = manager


def reset_memory_manager() -> None:
    """Reset any injected MemoryManager back to default."""
    global _shared_memory_manager
    _shared_memory_manager = None


def _active_memory_manager() -> MemoryManager:
    ctx = get_current_execution_context()
    if ctx is not None and ctx.memory_manager is not None:
        return ctx.memory_manager
    if _shared_memory_manager is not None:
        return _shared_memory_manager
    return get_memory_manager()


# Backward-compatibility alias for internal references
class _MemoryProxy:
    def __getattr__(self, name: str) -> Any:
        return getattr(_active_memory_manager(), name)


_memory = _MemoryProxy()



def remember(
    category: str,
    key: str,
    value: str,
) -> str:
    return _active_memory_manager().remember(
        category=category,
        key=key,
        value=value,
    )


def recall_memory(
    category: str,
    key: str,
) -> str:
    value = _active_memory_manager().recall(
        category=category,
        key=key,
    )

    if value is None:
        return f"I don't have a memory for '{key}' in '{category}'."

    return value


def list_memories(
    category: str | None = None,
) -> str:
    memories = _active_memory_manager().list_memories(category=category)

    if not memories:
        return "RIYA has no stored memories."

    lines = [
        f"{memory['category']}: {memory['key']} = {memory['value']}"
        for memory in memories
    ]
    return "\n".join(lines)


def forget_memory(
    category: str,
    key: str,
) -> str:
    deleted = _active_memory_manager().forget(
        category=category,
        key=key,
    )

    if not deleted:
        return f"I don't have a memory for '{key}' in '{category}'."

    return f"Forgot '{key}' from '{category}'."


def search_memory(
    query: str,
    category: str | None = None,
    limit: int = 5,
) -> str:
    """Search stored memories for the active user."""
    results = _active_memory_manager().search(
        query=query,
        category=category,
        limit=limit,
        use_semantic=True,
        record_access=True,
    )
    if not results:
        return f"No stored memories matched '{query}'."

    lines = [
        f"{item['category']}: {item['key']} = {item['value']}"
        for item in results
    ]
    return "\n".join(lines)
