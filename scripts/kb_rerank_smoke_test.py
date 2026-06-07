"""Compare embedding-only vs reranked chunk order for a sample query."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.kb_retrieval import refresh_kb_document_cache, retrieve_kb_hits
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
    _print_hits("Embedding pool (first 12)", pool[:12])
    _print_hits("After cross-encoder rerank", reranked)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
