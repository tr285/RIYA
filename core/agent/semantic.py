from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer


@dataclass
class SemanticMatch:
    tool_name: str
    description: str
    score: float


class SemanticToolEngine:
    """
    RIYA local semantic understanding engine.

    Uses a pretrained embedding model to determine which
    RIYA tools are semantically relevant to a user request.

    Important:

        Embeddings DO NOT execute tools.

    They only retrieve the tools that are most relevant.

    Execution remains controlled by:

        SecurityManager
            ↓
        ToolExecutor
            ↓
        Verification
    """

    MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

    def __init__(
        self,
        tool_registry,
        model_name: str = MODEL_NAME,
        top_k: int = 5,
    ):
        self.tools = tool_registry
        self.top_k = max(1, top_k)

        self.model = self._load_model(
            model_name
        )

        self._tool_texts = []
        self._tool_names = []
        self._tool_embeddings = None

        self._build_index()

    # =========================================================
    # MODEL
    # =========================================================

    def _load_model(
        self,
        model_name: str,
    ):
        """
        Load the pretrained embedding model.

        First execution may download the model.
        Later executions use the local cache.
        """

        try:
            return SentenceTransformer(
                model_name
            )

        except Exception as exc:

            raise RuntimeError(
                "RIYA could not load the local "
                "embedding model.\n\n"
                "The first run requires internet access "
                "to download the pretrained model.\n"
                "After that, RIYA can use the cached model "
                "offline.\n\n"
                f"Original error: {exc}"
            ) from exc

    # =========================================================
    # BUILD TOOL INDEX
    # =========================================================

    def _build_index(self):
        """
        Convert RIYA's registered tools into embeddings.
        """

        tools = self.tools.list_tools()

        if not tools:
            raise RuntimeError(
                "RIYA has no tools available for "
                "semantic indexing."
            )

        self._tool_texts = []
        self._tool_names = []

        for tool in tools:

            text = self._build_tool_text(
                tool
            )

            self._tool_texts.append(
                text
            )

            self._tool_names.append(
                tool.name
            )

        self._tool_embeddings = (
            self.model.encode(
                self._tool_texts,
                normalize_embeddings=True,
                convert_to_numpy=True,
            )
        )

    # =========================================================
    # TOOL DESCRIPTION
    # =========================================================

    def _build_tool_text(
        self,
        tool,
    ) -> str:
        """
        Build semantic information for a tool.

        This is NOT a list of command rules.

        It describes what the capability means.
        """

        parameters = ", ".join(
            sorted(tool.parameters)
        )

        return (
            f"Tool: {tool.name}. "
            f"Capability: {tool.description}. "
            f"Parameters: {parameters}."
        )

    # =========================================================
    # RETRIEVE
    # =========================================================

    def retrieve(
        self,
        user_request: str,
        top_k: int | None = None,
    ) -> list[SemanticMatch]:
        """
        Retrieve the most semantically relevant tools.
        """

        request = user_request.strip()

        if not request:
            return []

        if self._tool_embeddings is None:
            self._build_index()

        k = top_k or self.top_k

        k = min(
            k,
            len(self._tool_names),
        )

        query_embedding = (
            self.model.encode(
                request,
                normalize_embeddings=True,
                convert_to_numpy=True,
            )
        )

        scores = np.dot(
            self._tool_embeddings,
            query_embedding,
        )

        ranked_indices = np.argsort(
            scores
        )[::-1][:k]

        matches = []

        for index in ranked_indices:

            tool_name = (
                self._tool_names[index]
            )

            tool = self.tools.get(
                tool_name
            )

            matches.append(
                SemanticMatch(
                    tool_name=tool_name,
                    description=tool.description,
                    score=float(
                        scores[index]
                    ),
                )
            )

        return matches

    # =========================================================
    # BEST TOOL
    # =========================================================

    def best_match(
        self,
        user_request: str,
    ) -> SemanticMatch | None:

        matches = self.retrieve(
            user_request,
            top_k=1,
        )

        if not matches:
            return None

        return matches[0]

    # =========================================================
    # RELEVANT TOOL TEXT
    # =========================================================

    def relevant_tool_descriptions(
        self,
        user_request: str,
        top_k: int | None = None,
    ) -> str:
        """
        Return the most relevant tools in a format that
        Qwen3 can use for structured planning.
        """

        matches = self.retrieve(
            user_request,
            top_k=top_k,
        )

        if not matches:
            return ""

        lines = []

        for match in matches:

            tool = self.tools.get(
                match.tool_name
            )

            parameters = ", ".join(
                sorted(tool.parameters)
            )

            if parameters:

                lines.append(
                    f"- {tool.name}"
                    f"({parameters}): "
                    f"{tool.description}"
                    f" [semantic_score="
                    f"{match.score:.3f}]"
                )

            else:

                lines.append(
                    f"- {tool.name}(): "
                    f"{tool.description}"
                    f" [semantic_score="
                    f"{match.score:.3f}]"
                )

        return "\n".join(
            lines
        )