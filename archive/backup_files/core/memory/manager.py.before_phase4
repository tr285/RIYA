from core.memory.database import MemoryDatabase


class MemoryManager:

    def __init__(self, database=None):
        self.database = database or MemoryDatabase()

    def remember(
        self,
        category: str,
        key: str,
        value: str,
    ) -> str:

        category = category.strip()
        key = key.strip()
        value = value.strip()

        if not category:
            raise ValueError(
                "Memory category cannot be empty."
            )

        if not key:
            raise ValueError(
                "Memory key cannot be empty."
            )

        if not value:
            raise ValueError(
                "Memory value cannot be empty."
            )

        with self.database.connect() as connection:

            connection.execute(
                """
                INSERT INTO memories (
                    category,
                    key,
                    value
                )
                VALUES (?, ?, ?)

                ON CONFLICT(category, key)
                DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    category,
                    key,
                    value,
                ),
            )

            connection.commit()

        return (
            f"Remembered '{key}' "
            f"under '{category}'."
        )

    def recall(
        self,
        category: str,
        key: str,
    ) -> str | None:

        category = category.strip()
        key = key.strip()

        with self.database.connect() as connection:

            row = connection.execute(
                """
                SELECT value
                FROM memories
                WHERE category = ?
                  AND key = ?
                """,
                (
                    category,
                    key,
                ),
            ).fetchone()

        if row is None:
            return None

        return row["value"]

    def search(
        self,
        query: str,
        category: str | None = None,
    ) -> list[dict]:

        query = query.strip()

        if not query:
            return []

        category_value = (
            category.strip()
            if category is not None
            else None
        )

        # Split the query into useful words.
        words = [
            word.lower()
            for word in query.split()
            if len(word) >= 2
        ]

        with self.database.connect() as connection:

            if category_value is None:

                rows = connection.execute(
                    """
                    SELECT
                        id,
                        category,
                        key,
                        value,
                        created_at,
                        updated_at
                    FROM memories
                    ORDER BY
                        updated_at DESC,
                        id DESC
                    """
                ).fetchall()

            else:

                rows = connection.execute(
                    """
                    SELECT
                        id,
                        category,
                        key,
                        value,
                        created_at,
                        updated_at
                    FROM memories
                    WHERE category = ?
                    ORDER BY
                        updated_at DESC,
                        id DESC
                    """,
                    (category_value,),
                ).fetchall()

        memories = [
            dict(row)
            for row in rows
        ]

        if not words:
            return memories

        matches = []

        for memory in memories:

            searchable = " ".join(
                [
                    memory["category"],
                    memory["key"],
                    memory["value"],
                ]
            ).lower()

            score = 0

            for word in words:

                if word in searchable:
                    score += 1

            if score > 0:

                memory["_score"] = score
                matches.append(memory)

        matches.sort(
            key=lambda item: (
                item["_score"],
                item["updated_at"],
                item["id"],
            ),
            reverse=True,
        )

        for memory in matches:
            memory.pop("_score", None)

        return matches

    def list_memories(
        self,
        category: str | None = None,
    ) -> list[dict]:

        if category is not None:
            category = category.strip()

        with self.database.connect() as connection:

            if category is None:

                rows = connection.execute(
                    """
                    SELECT
                        id,
                        category,
                        key,
                        value,
                        created_at,
                        updated_at
                    FROM memories
                    ORDER BY
                        category,
                        key
                    """
                ).fetchall()

            else:

                rows = connection.execute(
                    """
                    SELECT
                        id,
                        category,
                        key,
                        value,
                        created_at,
                        updated_at
                    FROM memories
                    WHERE category = ?
                    ORDER BY key
                    """,
                    (category,),
                ).fetchall()

        return [
            dict(row)
            for row in rows
        ]

    def forget(
        self,
        category: str,
        key: str,
    ) -> bool:

        category = category.strip()
        key = key.strip()

        with self.database.connect() as connection:

            cursor = connection.execute(
                """
                DELETE FROM memories
                WHERE category = ?
                  AND key = ?
                """,
                (
                    category,
                    key,
                ),
            )

            connection.commit()

        return cursor.rowcount > 0

    def clear_category(
        self,
        category: str,
    ) -> int:

        category = category.strip()

        with self.database.connect() as connection:

            cursor = connection.execute(
                """
                DELETE FROM memories
                WHERE category = ?
                """,
                (category,),
            )

            connection.commit()

        return cursor.rowcount