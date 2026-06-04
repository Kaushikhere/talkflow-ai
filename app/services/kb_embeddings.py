from __future__ import annotations

import logging
import os

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from app.config import CHROMA_PATH, KB_TOP_K

logger = logging.getLogger(__name__)

COLLECTION_NAME = "talkflow_kb"
SMOKE_COLLECTION_NAME = "talkflow_kb_smoke"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

_client: chromadb.PersistentClient | None = None
_collection: chromadb.Collection | None = None
_embedding_fn: SentenceTransformerEmbeddingFunction | None = None


def _embedding_function() -> SentenceTransformerEmbeddingFunction:
    """Load embedding model once; prefer local cache to avoid Hugging Face SSL/network on each chat."""
    global _embedding_fn
    if _embedding_fn is not None:
        return _embedding_fn

    try:
        os.environ["HF_HUB_OFFLINE"] = "1"
        _embedding_fn = SentenceTransformerEmbeddingFunction(
            model_name=EMBEDDING_MODEL,
            model_kwargs={"local_files_only": True},
        )
        logger.info("Loaded embedding model from local cache: %s", EMBEDDING_MODEL)
        return _embedding_fn
    except Exception as local_exc:
        logger.warning(
            "Local embedding load failed (%s), trying online download once",
            local_exc,
        )

    _embedding_fn = SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL)
    logger.info("Loaded embedding model (online): %s", EMBEDDING_MODEL)
    return _embedding_fn


def warmup_kb_embeddings() -> None:
    """Pre-load Chroma client, embedding model, and one query (avoids slow first chat)."""
    get_collection()
    try:
        query_chunks("health insurance", top_k=1)
    except Exception as exc:
        logger.warning("KB warmup query failed: %s", exc)
    logger.info("KB embeddings warmed up")


def get_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        CHROMA_PATH.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    return _client


def get_collection(name: str = COLLECTION_NAME) -> chromadb.Collection:
    global _collection
    if name != COLLECTION_NAME:
        client = get_client()
        return client.get_or_create_collection(
            name=name,
            embedding_function=_embedding_function(),
        )
    if _collection is None:
        client = get_client()
        _collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=_embedding_function(),
        )
    return _collection


def reset_collection(name: str = COLLECTION_NAME) -> chromadb.Collection:
    global _collection
    client = get_client()
    try:
        client.delete_collection(name)
    except (ValueError, chromadb.errors.NotFoundError):
        pass
    col = client.get_or_create_collection(
        name=name,
        embedding_function=_embedding_function(),
    )
    if name == COLLECTION_NAME:
        _collection = col
    return col


def chroma_has_document_chunks(document_id: int) -> bool:
    collection = get_collection()
    result = collection.get(where={"document_id": document_id}, limit=1)
    return bool(result.get("ids"))


def delete_chunks_for_document(document_id: int) -> None:
    collection = get_collection()
    collection.delete(where={"document_id": document_id})


def add_chunks(
    chunks: list[str],
    *,
    ids: list[str] | None = None,
    metadatas: list[dict] | None = None,
) -> None:
    if not chunks:
        return

    collection = get_collection()
    doc_ids = ids if ids is not None else [f"chunk_{i}" for i in range(len(chunks))]
    meta = metadatas if metadatas is not None else [{"index": i} for i in range(len(chunks))]
    collection.add(documents=chunks, ids=doc_ids, metadatas=meta)


def _parse_query_result(result: dict) -> list[dict]:
    documents = result.get("documents") or [[]]
    distances = result.get("distances") or [[]]
    metadatas = result.get("metadatas") or [[]]
    ids = result.get("ids") or [[]]
    if not documents or not documents[0]:
        return []

    hits: list[dict] = []
    for i in range(len(documents[0])):
        hits.append(
            {
                "id": ids[0][i],
                "text": documents[0][i],
                "distance": distances[0][i],
                "metadata": metadatas[0][i],
            }
        )
    return hits


def query_chunks(query: str, *, top_k: int | None = None) -> list[dict]:
    n = top_k if top_k is not None else KB_TOP_K
    collection = get_collection()
    result = collection.query(query_texts=[query], n_results=n)
    return _parse_query_result(result)


def query_chunks_for_document(
    document_id: int,
    query: str,
    *,
    top_k: int = 3,
) -> list[dict]:
    """Semantic search limited to one KB document (used when title matches the query)."""
    collection = get_collection()
    result = collection.query(
        query_texts=[query],
        n_results=top_k,
        where={"document_id": document_id},
    )
    return _parse_query_result(result)
