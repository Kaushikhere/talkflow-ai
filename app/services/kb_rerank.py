"""Cross-encoder reranking for KB retrieval candidates."""
from __future__ import annotations

import logging
import os

from app.config import KB_RERANK_ENABLED, KB_RERANK_MODEL

logger = logging.getLogger(__name__)

MAX_RERANK_CHARS = 512

_reranker = None
_reranker_unavailable = False


def _load_cross_encoder():
    from sentence_transformers import CrossEncoder

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
                "Reranker unavailable (%s); falling back to embedding order",
                exc,
            )
            return None
    return _reranker


def warmup_kb_reranker() -> None:
    if not KB_RERANK_ENABLED:
        return
    model = get_reranker()
    if model is None:
        return
    model.predict([("warmup query", "warmup document text")])
    logger.info("KB reranker warmed up")


def rerank_hits(query: str, hits: list[dict], *, top_k: int) -> list[dict]:
    """Score (query, chunk) pairs and return the top_k most relevant hits."""
    if not KB_RERANK_ENABLED or not hits:
        return hits[:top_k]
    if len(hits) <= top_k:
        return hits

    cleaned = query.strip()
    if not cleaned:
        return hits[:top_k]

    model = get_reranker()
    if model is None:
        return hits[:top_k]

    pairs = [
        (cleaned, ((hit.get("text") or "").strip())[:MAX_RERANK_CHARS])
        for hit in hits
    ]
    scores = model.predict(pairs)

    ranked: list[dict] = []
    for hit, score in zip(hits, scores):
        updated = dict(hit)
        updated["rerank_score"] = float(score)
        ranked.append(updated)

    ranked.sort(key=lambda h: h.get("rerank_score", -999.0), reverse=True)
    return ranked[:top_k]
