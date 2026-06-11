"""Cross-encoder reranking for KB retrieval candidates."""
from __future__ import annotations

import logging
import os

from app.config import (
    KB_RERANK_BATCH_SIZE,
    KB_RERANK_ENABLED,
    KB_RERANK_MAX_CHARS,
    KB_RERANK_MODEL,
)

logger = logging.getLogger(__name__)

_reranker = None
_reranker_unavailable = False


def _limit_cpu_threads() -> None:
    """Avoid oversubscribing CPU during cross-encoder inference."""
    try:
        import torch

        threads = int(os.getenv("KB_RERANK_THREADS", "2"))
        torch.set_num_threads(max(1, threads))
    except ImportError:
        pass


def _load_cross_encoder():
    from sentence_transformers import CrossEncoder

    _limit_cpu_threads()
    try:
        os.environ["HF_HUB_OFFLINE"] = "1"
        model = CrossEncoder(
            KB_RERANK_MODEL,
            model_kwargs={"local_files_only": True},
        )
        logger.info("Loaded reranker from local cache: %s", KB_RERANK_MODEL)
        return model
    except Exception as local_exc:
        logger.warning(
            "Local reranker load failed (%s), trying online download once",
            local_exc,
        )

    os.environ.pop("HF_HUB_OFFLINE", None)
    model = CrossEncoder(KB_RERANK_MODEL)
    logger.info("Loaded reranker (online): %s", KB_RERANK_MODEL)
    return model


def get_reranker():
    global _reranker, _reranker_unavailable
    if _reranker_unavailable:
        return None
    if _reranker is None:
        try:
            _reranker = _load_cross_encoder()
        except Exception as exc:
            _reranker_unavailable = True
            logger.error(
                "Reranker unavailable for %s (%s); falling back to embedding order",
                KB_RERANK_MODEL,
                exc,
            )
            return None
    return _reranker


def reset_reranker() -> None:
    """Clear cached model (used after config changes in tests)."""
    global _reranker, _reranker_unavailable
    _reranker = None
    _reranker_unavailable = False


def warmup_kb_reranker() -> None:
    if not KB_RERANK_ENABLED:
        return
    model = get_reranker()
    if model is None:
        return
    model.predict(
        [("warmup query", "warmup document text")],
        batch_size=1,
        show_progress_bar=False,
    )
    logger.info("KB reranker warmed up (%s)", KB_RERANK_MODEL)


def _passage_for_rerank(hit: dict) -> str:
    """Build passage text for cross-encoder; include product title when available."""
    text = (hit.get("text") or "").strip()
    meta = hit.get("metadata") or {}
    title = (meta.get("title") or "").strip()
    if title and not text.lower().startswith("document:"):
        passage = f"{title}. {text}"
    else:
        passage = text
    return passage[:KB_RERANK_MAX_CHARS]


def rerank_hits(query: str, hits: list[dict], *, top_k: int) -> list[dict]:
    """Score (query, chunk) pairs with a cross-encoder and return the top_k hits."""
    if not KB_RERANK_ENABLED or not hits:
        return hits[:top_k]

    cleaned = query.strip()
    if not cleaned:
        return hits[:top_k]

    model = get_reranker()
    if model is None:
        return hits[:top_k]

    pairs = [(cleaned, _passage_for_rerank(hit)) for hit in hits]
    scores = model.predict(
        pairs,
        batch_size=KB_RERANK_BATCH_SIZE,
        show_progress_bar=False,
    )

    ranked: list[dict] = []
    for hit, score in zip(hits, scores):
        updated = dict(hit)
        updated["rerank_score"] = float(score)
        ranked.append(updated)

    ranked.sort(key=lambda h: h.get("rerank_score", -999.0), reverse=True)
    return ranked[:top_k]
