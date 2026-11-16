"""Offline semantic + multi-signal relevance scoring engine for RIYA memories."""

from __future__ import annotations

from datetime import datetime, timezone
import math
import re
import threading
from typing import Any


_WORD_RE = re.compile(r"[a-z0-9]+")
_EMBEDDING_CACHE_LOCK = threading.Lock()
_CACHED_EMBEDDING_MODEL: Any = None
_CACHED_MODEL_ATTEMPTED: bool = False


def _tokenize(text: str) -> list[str]:
    if not isinstance(text, str):
        return []
    return _WORD_RE.findall(text.lower())


def _char_trigrams(text: str) -> set[str]:
    cleaned = " ".join(text.lower().split())
    if len(cleaned) < 3:
        return {cleaned} if cleaned else set()
    return {cleaned[i : i + 3] for i in range(len(cleaned) - 2)}


def lexical_similarity(query: str, target: str) -> float:
    """Compute bounded [0.0, 1.0] lexical + trigram similarity without external deps."""
    q_clean = " ".join(str(query or "").lower().split())
    t_clean = " ".join(str(target or "").lower().split())
    if not q_clean or not t_clean:
        return 0.0
    if q_clean == t_clean:
        return 1.0
    if q_clean in t_clean:
        # Substring match gets a strong baseline plus coverage bonus.
        ratio = len(q_clean) / max(len(t_clean), 1)
        return min(0.98, 0.72 + 0.25 * ratio)

    q_words = set(_tokenize(q_clean))
    t_words = set(_tokenize(t_clean))
    word_jaccard = (
        len(q_words & t_words) / max(len(q_words | t_words), 1)
        if q_words and t_words
        else 0.0
    )
    word_recall = (
        len(q_words & t_words) / max(len(q_words), 1)
        if q_words
        else 0.0
    )

    q_tri = _char_trigrams(q_clean)
    t_tri = _char_trigrams(t_clean)
    tri_dice = (
        (2.0 * len(q_tri & t_tri)) / max(len(q_tri) + len(t_tri), 1)
        if q_tri and t_tri
        else 0.0
    )

    return min(1.0, max(word_recall * 0.75 + word_jaccard * 0.25, tri_dice * 0.75))


def get_cached_local_embedding_model(*, allow_load: bool = True) -> Any | None:
    """
    Return the cached local sentence-transformers model using local_files_only=True.

    Never makes network requests. Returns None cleanly if unavailable offline.
    """
    global _CACHED_EMBEDDING_MODEL, _CACHED_MODEL_ATTEMPTED
    with _EMBEDDING_CACHE_LOCK:
        if _CACHED_EMBEDDING_MODEL is not None:
            return _CACHED_EMBEDDING_MODEL
        if not allow_load or _CACHED_MODEL_ATTEMPTED:
            return None
        _CACHED_MODEL_ATTEMPTED = True
        try:
            from sentence_transformers import SentenceTransformer

            _CACHED_EMBEDDING_MODEL = SentenceTransformer(
                "sentence-transformers/all-MiniLM-L6-v2",
                local_files_only=True,
            )
        except Exception:
            _CACHED_EMBEDDING_MODEL = None
        return _CACHED_EMBEDDING_MODEL


def set_shared_embedding_model(model: Any | None) -> None:
    """Allow RIYAAssistant/SemanticToolEngine to share its already-loaded model."""
    global _CACHED_EMBEDDING_MODEL, _CACHED_MODEL_ATTEMPTED
    with _EMBEDDING_CACHE_LOCK:
        _CACHED_EMBEDDING_MODEL = model
        if model is not None:
            _CACHED_MODEL_ATTEMPTED = True


def _cosine_similarity(vec_a: Any, vec_b: Any) -> float:
    try:
        dot = float(sum(float(a) * float(b) for a, b in zip(vec_a, vec_b)))
        norm_a = math.sqrt(sum(float(a) * float(a) for a in vec_a))
        norm_b = math.sqrt(sum(float(b) * float(b) for b in vec_b))
        if norm_a <= 0.0 or norm_b <= 0.0:
            return 0.0
        return max(0.0, min(1.0, dot / (norm_a * norm_b)))
    except Exception:
        return 0.0


def _recency_score(updated_at: str | None, now: datetime) -> float:
    if not updated_at:
        return 0.3
    try:
        raw = str(updated_at).strip()
        if "T" in raw:
            dt = datetime.fromisoformat(raw)
        else:
            dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
        return math.exp(-age_days / 30.0)
    except Exception:
        return 0.3


class MemorySemanticScorer:
    """Offline hybrid scorer combining semantic embeddings, lexical match, importance, recency, and usage."""

    def __init__(self, embedding_model: Any | None = None, *, enable_embeddings: bool = True):
        self._explicit_model = embedding_model
        self.enable_embeddings = bool(enable_embeddings)

    def _model(self) -> Any | None:
        if self._explicit_model is not None:
            return self._explicit_model
        if not self.enable_embeddings:
            return None
        return get_cached_local_embedding_model(allow_load=True)

    def pair_similarity(self, text_a: str, text_b: str) -> float:
        """Return symmetric [0.0, 1.0] similarity between two texts (used for near-duplicate detection)."""
        lex = max(
            lexical_similarity(text_a, text_b),
            lexical_similarity(text_b, text_a),
        )
        model = self._model()
        if model is None:
            return lex
        try:
            vecs = model.encode(
                [text_a, text_b],
                normalize_embeddings=True,
                convert_to_numpy=False,
            )
            sem = _cosine_similarity(vecs[0], vecs[1])
            return max(lex, sem)
        except Exception:
            return lex

    def rank_memories(
        self,
        query: str,
        memories: list[dict[str, Any]],
        *,
        use_semantic: bool = True,
        min_match_score: float = 0.24,
        min_semantic_only_score: float = 0.30,
    ) -> list[dict[str, Any]]:
        """Score and sort memory dicts descending by relevance."""
        if not memories or not str(query or "").strip():
            return []

        now = datetime.now(timezone.utc)
        doc_texts = [
            (
                f"{str(m.get('category', '')).replace('_', ' ')} "
                f"{str(m.get('key', '')).replace('_', ' ')}: "
                f"{m.get('value', '')} {' '.join(m.get('tags') or [])}"
            ).strip()
            for m in memories
        ]

        sem_scores: list[float] | None = None
        model = self._model() if use_semantic else None
        if model is not None:
            try:
                q_vec = model.encode(
                    query,
                    normalize_embeddings=True,
                    convert_to_numpy=False,
                )
                doc_vecs = model.encode(
                    doc_texts,
                    normalize_embeddings=True,
                    convert_to_numpy=False,
                )
                sem_scores = [_cosine_similarity(q_vec, d_vec) for d_vec in doc_vecs]
            except Exception:
                sem_scores = None

        ranked: list[dict[str, Any]] = []
        for idx, mem in enumerate(memories):
            key_text = str(mem.get("key", ""))
            val_text = str(mem.get("value", ""))
            cat_text = str(mem.get("category", ""))
            searchable = f"{cat_text} {key_text} {val_text}".strip()

            lex_full = lexical_similarity(query, searchable)
            lex_key = lexical_similarity(query, key_text.replace("_", " "))
            lex_val = lexical_similarity(query, val_text)
            lex_score = max(lex_full, lex_key, lex_val)

            sem_score = sem_scores[idx] if sem_scores is not None else 0.0
            # Require real textual or semantic relevance before counting metadata boosts.
            if sem_scores is not None:
                if lex_score < min_match_score and sem_score < min_semantic_only_score:
                    continue
                relevance_component = 0.55 * sem_score + 0.45 * lex_score
            else:
                if lex_score < min_match_score:
                    continue
                relevance_component = lex_score

            importance = max(0.0, min(1.0, float(mem.get("importance", 0.5) or 0.5)))
            access_count = max(0, int(mem.get("access_count", 0) or 0))
            freq_bonus = min(1.0, access_count / 10.0)
            recency = _recency_score(mem.get("updated_at"), now)

            composite = (
                0.76 * relevance_component
                + 0.12 * importance
                + 0.07 * recency
                + 0.05 * freq_bonus
            )
            scored_item = dict(mem)
            scored_item["relevance_score"] = round(composite, 4)
            scored_item["semantic_score"] = round(sem_score if sem_scores is not None else lex_score, 4)
            ranked.append(scored_item)

        ranked.sort(
            key=lambda item: (
                item.get("relevance_score", 0.0),
                item.get("importance", 0.5),
                item.get("updated_at", ""),
                item.get("id", 0),
            ),
            reverse=True,
        )
        return ranked
