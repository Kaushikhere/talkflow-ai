"""Compare embedding-only vs reranked chunk order for a sample query."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.kb_retrieval import (
    _rank_candidates,
    _should_skip_rerank,
    refresh_kb_document_cache,
    retrieve_kb_hits,
)
from app.services.kb_rerank import rerank_hits

QUERY = "What is Secure Plus? List main features and benefits."


def _print_hits(label: str, hits: list[dict]) -> None:
    print(f"\n{label} ({len(hits)} hits)")
    for i, hit in enumerate(hits[:12], start=1):
        meta = hit.get("metadata") or {}
        title = meta.get("title", "?")
        page = meta.get("page_number")
        dist = hit.get("distance")
        score = hit.get("rerank_score")
        extra = f" rerank={score:.3f}" if score is not None else f" dist={dist:.3f}" if dist else ""
        print(f"  {i}. {title} p{page}{extra}")


def main() -> int:
    refresh_kb_document_cache()
    pool = retrieve_kb_hits(QUERY, top_k=40)
    reranked = rerank_hits(QUERY, pool, top_k=12)
    production_ranked = _rank_candidates(QUERY, pool)

    _print_hits("Embedding pool (first 12)", pool[:12])
    _print_hits("After cross-encoder rerank", reranked)

    head = production_ranked[: min(12, len(production_ranked))]
    if not head:
        print("\nFAIL: production _rank_candidates returned no hits")
        return 1

    if _should_skip_rerank(QUERY, pool):
        _print_hits("Production _rank_candidates (fast path, no rerank)", head)
        print("\nOK: rerank skipped — title-focused retrieval (expected fast path)")
        return 0

    missing_scores = [h for h in head if "rerank_score" not in h]
    if missing_scores:
        print(
            f"\nFAIL: {len(missing_scores)}/{len(head)} production hits lack rerank_score"
        )
        return 1

    embed_top = pool[:12]
    prod_top_ids = [h.get("id") for h in head]
    embed_top_ids = [h.get("id") for h in embed_top]
    order_changed = prod_top_ids != embed_top_ids
    _print_hits("Production _rank_candidates (top 12)", head)
    if order_changed:
        print("\nOK: reranking changed chunk order vs embedding-only")
    else:
        print("\nOK: rerank scores assigned (order unchanged for this query)")
    print("\nOK: production rerank path assigns rerank_score")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
