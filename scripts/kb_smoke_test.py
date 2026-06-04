"""Smoke test: embed dummy chunks and verify Chroma similarity search."""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.kb_chunking import chunk_text
from app.services.kb_embeddings import (
    SMOKE_COLLECTION_NAME,
    _embedding_function,
    get_client,
)

DUMMY_DOCS = [
    {
        "id": "asyncio_doc",
        "topic": "asyncio",
        "text": (
            "Python asyncio uses async and await to run coroutines on an event loop. "
            "Tasks and futures schedule concurrent I/O without blocking threads."
        ),
    },
    {
        "id": "baking_doc",
        "topic": "baking",
        "text": (
            "Sourdough bread needs a mature starter, autolyse, stretch and fold, "
            "and a long cold ferment before baking in a Dutch oven."
        ),
    },
    {
        "id": "postgres_doc",
        "topic": "postgres",
        "text": (
            "PostgreSQL B-tree indexes speed up WHERE clauses and JOIN keys. "
            "EXPLAIN ANALYZE shows sequential scans versus index scans on large tables."
        ),
    },
]

QUERIES = [
    {
        "query": "async await coroutine",
        "expected_topic": "asyncio",
        "needles": ("asyncio", "coroutine", "await"),
    },
    {
        "query": "database btree index",
        "expected_topic": "postgres",
        "needles": ("postgresql", "b-tree", "index"),
    },
]


def _topic_from_metadata(meta: dict | None) -> str | None:
    if not meta:
        return None
    return meta.get("topic")


def main() -> int:
    print(f"Using isolated smoke collection: {SMOKE_COLLECTION_NAME}")
    client = get_client()
    try:
        client.delete_collection(SMOKE_COLLECTION_NAME)
    except (ValueError, Exception):
        pass
    collection = client.get_or_create_collection(
        name=SMOKE_COLLECTION_NAME,
        embedding_function=_embedding_function(),
    )

    all_chunks: list[str] = []
    all_ids: list[str] = []
    all_meta: list[dict] = []

    for doc in DUMMY_DOCS:
        chunks = chunk_text(doc["text"])
        for i, chunk in enumerate(chunks):
            all_chunks.append(chunk)
            all_ids.append(f"{doc['id']}_{i}")
            all_meta.append({"topic": doc["topic"], "doc_id": doc["id"]})

    print(f"Adding {len(all_chunks)} chunks...")
    collection.add(documents=all_chunks, ids=all_ids, metadatas=all_meta)

    failed = False
    for spec in QUERIES:
        result = collection.query(query_texts=[spec["query"]], n_results=3)
        hits = []
        docs = result.get("documents") or [[]]
        dists = result.get("distances") or [[]]
        metas = result.get("metadatas") or [[]]
        ids = result.get("ids") or [[]]
        for i in range(len(docs[0])):
            hits.append(
                {
                    "id": ids[0][i],
                    "text": docs[0][i],
                    "distance": dists[0][i],
                    "metadata": metas[0][i],
                }
            )
        if not hits:
            print(f"FAIL: no results for query {spec['query']!r}")
            failed = True
            continue

        top = hits[0]
        top_topic = _topic_from_metadata(top.get("metadata"))
        text_lower = (top.get("text") or "").lower()
        topic_ok = top_topic == spec["expected_topic"]
        needle_ok = any(n in text_lower for n in spec["needles"])

        print(f"\nQuery: {spec['query']!r}")
        print(f"  Top id: {top.get('id')} distance: {top.get('distance')}")
        print(f"  Top topic: {top_topic} (expected {spec['expected_topic']})")
        print(f"  Snippet: {(top.get('text') or '')[:120]}...")

        if not (topic_ok or needle_ok):
            print("  FAIL: top hit is not the expected document")
            failed = True
        else:
            print("  PASS")

    if failed:
        print("\nSmoke test FAILED")
        return 1

    print("\nSmoke test PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
