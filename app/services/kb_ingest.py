import hashlib
import logging
from pathlib import Path

from app.config import BASE_DIR, KB_DATA_DIR, KB_DEFAULT_SOURCE_URL, KB_EXTERNAL_DIR, KB_SEED_URLS
from app.database import (
    create_kb_ingest_run,
    finish_kb_ingest_run,
    get_kb_document_by_source_url,
    insert_kb_document,
    update_kb_document,
)
from app.services.document_extraction import extract_pdf_metadata, extract_pdf_pages
from app.services.kb_chunking import chunk_text
from app.services.kb_embeddings import (
    add_chunks,
    chroma_has_document_chunks,
    delete_chunks_for_document,
)
from app.services.kb_scraper import scrape_care_pdfs

logger = logging.getLogger(__name__)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _resolve_pdf_path(pdf_path: Path) -> Path:
    path = pdf_path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Not a PDF file: {path}")
    return path


def _default_source_url(path: Path) -> str:
    try:
        rel = path.resolve().relative_to(BASE_DIR.resolve())
        return f"file://{rel.as_posix()}"
    except ValueError:
        return f"file://{path.resolve().as_posix()}"


def _default_title(path: Path) -> str:
    name = path.stem.replace("-", " ").replace("_", " ")
    return " ".join(name.split())


def ingest_pdf_from_path(
    pdf_path: Path,
    *,
    title: str | None = None,
    source_url: str | None = None,
    brochure_source_url: str | None = None,
    force: bool = False,
) -> dict:
    """
    Ingest a local PDF: extract per-page text, chunk, embed in Chroma, record in kb_documents.
    """
    path = _resolve_pdf_path(pdf_path)
    content_hash = _file_hash(path)
    doc_title = title or _default_title(path)
    src_url = source_url or _default_source_url(path)
    catalog_url = brochure_source_url or KB_DEFAULT_SOURCE_URL

    try:
        rel_raw = str(path.resolve().relative_to(BASE_DIR.resolve()))
    except ValueError:
        rel_raw = str(path.resolve())

    existing = get_kb_document_by_source_url(src_url)
    if (
        not force
        and existing
        and existing["content_hash"] == content_hash
        and existing["status"] == "indexed"
        and chroma_has_document_chunks(existing["id"])
    ):
        logger.info("PDF already indexed: %s", src_url)
        return {
            "document_id": existing["id"],
            "source_url": src_url,
            "title": existing["title"],
            "chunk_count": existing["chunk_count"],
            "status": "skipped",
        }

    if existing:
        document_id = existing["id"]
        update_kb_document(
            document_id,
            status="indexing",
            content_hash=content_hash,
            title=doc_title,
        )
    else:
        document_id = insert_kb_document(
            source_url=src_url,
            title=doc_title,
            content_hash=content_hash,
            raw_path=rel_raw,
            status="indexing",
        )

    metadata_extra = extract_pdf_metadata(path)
    pages = extract_pdf_pages(path)
    if not pages:
        update_kb_document(document_id, status="failed")
        raise ValueError(f"No text extracted from PDF: {path.name}")

    all_chunks: list[str] = []
    all_ids: list[str] = []
    all_metas: list[dict] = []

    title_prefix = f"Document: {doc_title}\n\n"

    for page_number, page_text in enumerate(pages, start=1):
        for chunk_index, chunk in enumerate(chunk_text(page_text)):
            all_chunks.append(title_prefix + chunk)
            all_ids.append(f"doc_{document_id}_p{page_number}_c{chunk_index}")
            all_metas.append(
                {
                    "title": doc_title,
                    "source_url": catalog_url,
                    "page_number": page_number,
                    "document_id": document_id,
                    "chunk_index": chunk_index,
                    "file_source_url": src_url,
                }
            )

    delete_chunks_for_document(document_id)
    add_chunks(all_chunks, ids=all_ids, metadatas=all_metas)

    update_kb_document(
        document_id,
        status="indexed",
        chunk_count=len(all_chunks),
    )

    logger.info(
        "Indexed %s: %d pages, %d chunks (method pages=%d)",
        path.name,
        len(pages),
        len(all_chunks),
        metadata_extra.get("pdf_pages"),
    )

    return {
        "document_id": document_id,
        "source_url": src_url,
        "title": doc_title,
        "chunk_count": len(all_chunks),
        "page_count": len(pages),
        "status": "indexed",
    }


def ingest_pdf_from_kb_dir(filename: str, **kwargs) -> dict:
    """Ingest a PDF already placed under data/kb/."""
    path = KB_DATA_DIR / filename
    return ingest_pdf_from_path(path, **kwargs)


def list_kb_pdf_paths() -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for folder in (KB_DATA_DIR, KB_EXTERNAL_DIR):
        if not folder.is_dir():
            continue
        for pdf in sorted(folder.glob("*.pdf")):
            key = str(pdf.resolve())
            if key in seen:
                continue
            seen.add(key)
            paths.append(pdf)
    return paths


def run_kb_pipeline(
    *,
    scrape: bool = True,
    ingest: bool = True,
    seed_urls: list[str] | None = None,
    brochure_html_path: Path | None = None,
    brochure_only: bool = False,
    force_reindex: bool = False,
) -> dict:
    """
    Full orchestration: scrape Care PDFs, then ingest all PDFs under data/kb/ and external/.
    Logs progress in kb_ingest_runs.
    """
    seeds = seed_urls or KB_SEED_URLS
    seed_snapshot = ",".join(seeds)
    run_id = create_kb_ingest_run(seed_snapshot)

    documents_added = 0
    chunks_added = 0
    scrape_summary: dict = {"discovered": 0, "downloaded": 0, "pages_visited": 0}
    ingest_results: list[dict] = []

    try:
        source_by_path: dict[Path, tuple[str, str | None]] = {}

        if scrape:
            logger.info("Starting Care PDF scrape from %d seed URL(s)", len(seeds))
            scrape_result = scrape_care_pdfs(
                seeds,
                brochure_html_path=brochure_html_path,
                brochure_only=brochure_only,
            )
            scrape_summary = {
                "discovered": len(scrape_result.discovered_urls),
                "downloaded": len(scrape_result.downloaded),
                "pages_visited": scrape_result.pages_visited,
            }
            for item in scrape_result.downloaded:
                source_by_path[item.path.resolve()] = (item.url, item.title)

        if ingest:
            for pdf_path in list_kb_pdf_paths():
                meta = source_by_path.get(pdf_path.resolve())
                kwargs: dict = {}
                if meta:
                    kwargs["source_url"] = meta[0]
                    if meta[1]:
                        kwargs["title"] = meta[1]

                if force_reindex:
                    kwargs["force"] = True
                result = ingest_pdf_from_path(pdf_path, **kwargs)
                ingest_results.append(result)
                if result["status"] == "indexed":
                    documents_added += 1
                    chunks_added += int(result.get("chunk_count") or 0)

        finish_kb_ingest_run(
            run_id,
            status="completed",
            documents_added=documents_added,
            chunks_added=chunks_added,
        )

        try:
            from app.services.kb_retrieval import refresh_kb_document_cache

            refresh_kb_document_cache()
        except Exception:
            logger.debug("KB title cache refresh skipped", exc_info=True)

        return {
            "run_id": run_id,
            "status": "completed",
            "scrape": scrape_summary,
            "documents_added": documents_added,
            "chunks_added": chunks_added,
            "ingest_count": len(ingest_results),
            "ingest_results": ingest_results,
        }
    except Exception as exc:
        logger.exception("KB pipeline failed")
        finish_kb_ingest_run(
            run_id,
            status="failed",
            documents_added=documents_added,
            chunks_added=chunks_added,
            error_message=str(exc),
        )
        raise
