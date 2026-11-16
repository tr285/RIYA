from core.memory.manager import MemoryManager


_memory = MemoryManager()


def remember(
    category: str,
    key: str,
    value: str,
) -> str:

    return _memory.remember(
        category=category,
        key=key,
        value=value,
    )


def recall_memory(
    category: str,
    key: str,
) -> str:

    value = _memory.recall(
        category=category,
        key=key,
    )

    if value is None:
        return (
            f"I don't have a memory for "
            f"'{key}' in '{category}'."
        )

    return value


def list_memories(
    category: str | None = None,
) -> str:

    memories = _memory.list_memories(
        category=category
    )

    if not memories:
        return (
            "RIYA has no stored memories."
        )

    lines = []

    for memory in memories:

        lines.append(
            f"{memory['category']}: "
            f"{memory['key']} = "
            f"{memory['value']}"
        )

    return "\n".join(lines)


def forget_memory(
    category: str,
    key: str,
) -> str:

    deleted = _memory.forget(
        category=category,
        key=key,
    )

    if not deleted:
        return (
            f"I don't have a memory for "
            f"'{key}' in '{category}'."
        )

    return (
        f"Forgot '{key}' from "
        f"'{category}'."
    )