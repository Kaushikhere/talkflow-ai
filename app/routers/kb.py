import logging
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from app.config import (
    ADMIN_API_KEY,
    BASE_DIR,
    KB_DATA_DIR,
    KB_ENABLED,
    KB_EXTERNAL_DIR,
    KB_RERANK_ENABLED,
    KB_RERANK_MODEL,
    KB_RERANK_POOL,
    KB_RETRIEVE_POOL,
    KB_TOP_K,
    MAX_UPLOAD_BYTES,
)
from app.database import (
    count_indexed_docs_missing_chroma,
    delete_kb_document,
    get_kb_document_by_id,
    get_kb_stats,
    get_last_kb_ingest_run,
    get_running_kb_ingest_run,
    list_kb_documents,
)
from app.models import KbIngestRequest, PolicyRecommendRequest
from app.services.document_extraction import extract_pdf_pages
from app.services.kb_embeddings import (
    chroma_chunk_count,
    chroma_has_document_chunks,
    delete_chunks_for_document,
)
from app.services.kb_ingest import ingest_pdf_from_path, run_kb_pipeline
from app.services.kb_retrieval import refresh_kb_document_cache
from app.services.kb_tasks import submit_kb_job
from app.services.policy_recommend import recommend_policies

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kb", tags=["knowledge-base"])

UPLOAD_CHUNK_SIZE = 1024 * 1024
_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def _require_admin(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")) -> None:
    if not ADMIN_API_KEY:
        return
    if x_admin_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Key")


def _require_kb_enabled() -> None:
    if not KB_ENABLED:
        raise HTTPException(
            status_code=400,
            detail="Knowledge base is disabled (set KB_ENABLED=true in .env).",
        )


def _safe_external_filename(original: str) -> str:
    stem = Path(original or "document.pdf").stem.strip() or "document"
    stem = _SAFE_NAME_RE.sub("-", stem).strip("-._")[:100] or "document"
    return f"{stem}.pdf"


def _unique_external_path(filename: str) -> Path:
    KB_EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    candidate = KB_EXTERNAL_DIR / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    for _ in range(100):
        alt = KB_EXTERNAL_DIR / f"{stem}-{uuid.uuid4().hex[:8]}{suffix}"
        if not alt.exists():
            return alt
    raise HTTPException(status_code=500, detail="Could not allocate unique filename")


def _resolve_kb_pdf_path(raw_path: str) -> Path:
    if not raw_path:
        raise HTTPException(status_code=404, detail="Document path not set")
    candidate = (BASE_DIR / raw_path).resolve()
    kb_root = KB_DATA_DIR.resolve()
    try:
        candidate.relative_to(kb_root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Invalid document path") from exc
    if not candidate.is_file() or candidate.suffix.lower() != ".pdf":
        raise HTTPException(status_code=404, detail="PDF file not found")
    return candidate


def _run_ingest_job(
    *,
    scrape: bool,
    force_reindex: bool,
    brochure_only: bool,
    brochure_html_path: Path | None,
) -> None:
    try:
        run_kb_pipeline(
            scrape=scrape,
            ingest=True,
            brochure_only=brochure_only,
            brochure_html_path=brochure_html_path,
            force_reindex=force_reindex,
        )
        refresh_kb_document_cache()
    except Exception:
        logger.exception("Background KB ingest failed")


def _schedule_ingest_job(
    *,
    scrape: bool,
    force_reindex: bool,
    brochure_only: bool,
    brochure_html_path: Path | None,
) -> None:
    submit_kb_job(
        _run_ingest_job,
        scrape=scrape,
        force_reindex=force_reindex,
        brochure_only=brochure_only,
        brochure_html_path=brochure_html_path,
    )


@router.get("/status")
def kb_status(light: bool = Query(default=False)):
    stats = get_kb_stats()
    last_run = get_last_kb_ingest_run()
    running = get_running_kb_ingest_run()
    drift_count = 0
    chroma_count = 0
    if KB_ENABLED and not light:
        try:
            chroma_count = chroma_chunk_count()
            drift_count = count_indexed_docs_missing_chroma(chroma_has_document_chunks)
        except Exception:
            logger.exception("KB health check failed")

    return {
        "enabled": KB_ENABLED,
        "top_k": KB_TOP_K,
        "retrieve_pool": KB_RETRIEVE_POOL,
        "rerank_pool": KB_RERANK_POOL,
        "rerank_enabled": KB_RERANK_ENABLED,
        "rerank_model": KB_RERANK_MODEL,
        "documents_total": stats["documents_total"],
        "documents_indexed": stats["documents_indexed"],
        "chunks_total": stats["chunks_total"],
        "chroma_chunk_count": chroma_count,
        "drift_count": drift_count,
        "ingest_running": running is not None,
        "running_run_id": running["id"] if running else None,
        "last_run": last_run,
    }


@router.get("/documents/{document_id}/file")
def kb_document_file(document_id: int, page: int | None = Query(default=None, ge=1)):
    doc = get_kb_document_by_id(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    pdf_path = _resolve_kb_pdf_path(doc["raw_path"])
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=pdf_path.name,
        headers={"X-Page-Number": str(page)} if page else {},
    )


@router.get("/documents/{document_id}/page-text")
def kb_document_page_text(document_id: int, page: int = Query(ge=1)):
    doc = get_kb_document_by_id(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    pdf_path = _resolve_kb_pdf_path(doc["raw_path"])
    pages = extract_pdf_pages(pdf_path)
    if page > len(pages):
        raise HTTPException(status_code=404, detail="Page not found")
    return {
        "document_id": document_id,
        "title": doc["title"],
        "page": page,
        "text": pages[page - 1],
    }


@router.post("/recommend")
def kb_recommend(request: PolicyRecommendRequest):
    if not KB_ENABLED:
        raise HTTPException(
            status_code=400,
            detail="Knowledge base is disabled (set KB_ENABLED=true in .env).",
        )
    return recommend_policies(
        age=request.age,
        budget_monthly=request.budget_monthly,
        pre_existing=request.pre_existing,
        family_size=request.family_size,
        priorities=request.priorities,
    )


def _run_upload_ingest(
    pdf_path: Path,
    *,
    title: str | None,
    source_url: str,
) -> None:
    try:
        ingest_pdf_from_path(
            pdf_path,
            title=title,
            source_url=source_url,
            force=False,
        )
        refresh_kb_document_cache()
    except Exception:
        logger.exception("Background KB upload ingest failed for %s", pdf_path.name)


@router.post("/documents/upload")
async def upload_kb_document(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    _require_admin(x_admin_key)
    _require_kb_enabled()

    safe_name = Path(file.filename or "document.pdf").name
    if not safe_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported for the knowledge base.")

    dest = _unique_external_path(_safe_external_filename(safe_name))
    size = 0
    try:
        with dest.open("wb") as output:
            while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    dest.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=400,
                        detail=f"File too large. Maximum size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                    )
                output.write(chunk)
    finally:
        await file.close()

    rel = str(dest.resolve().relative_to(BASE_DIR.resolve())).replace("\\", "/")
    source_url = f"file://{rel}"
    doc_title = title.strip() if title and title.strip() else None

    submit_kb_job(
        _run_upload_ingest,
        dest,
        title=doc_title,
        source_url=source_url,
    )

    return {
        "filename": dest.name,
        "size": size,
        "status": "indexing",
        "title": doc_title or dest.stem,
        "message": "Upload saved. Indexing in background.",
    }


@router.delete("/documents/{document_id}")
def delete_kb_document_route(
    document_id: int,
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    _require_admin(x_admin_key)
    _require_kb_enabled()

    doc = get_kb_document_by_id(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    try:
        delete_chunks_for_document(document_id)
    except Exception as exc:
        logger.warning("Chroma delete failed for doc %s: %s", document_id, exc)

    raw_path = doc.get("raw_path")
    if raw_path:
        try:
            pdf_path = _resolve_kb_pdf_path(raw_path)
            pdf_path.unlink(missing_ok=True)
        except HTTPException:
            pass

    if not delete_kb_document(document_id):
        raise HTTPException(status_code=404, detail="Document not found")

    refresh_kb_document_cache()
    return {"deleted": True, "document_id": document_id}


@router.post("/documents/{document_id}/reindex")
def reindex_kb_document(
    document_id: int,
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    _require_admin(x_admin_key)
    _require_kb_enabled()

    doc = get_kb_document_by_id(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    pdf_path = _resolve_kb_pdf_path(doc["raw_path"])
    try:
        result = ingest_pdf_from_path(
            pdf_path,
            title=doc.get("title"),
            source_url=doc.get("source_url"),
            force=True,
        )
        refresh_kb_document_cache()
        return result
    except Exception as exc:
        logger.exception("KB reindex failed for doc %s", document_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/ingest/reindex-all")
def reindex_all_kb_documents(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    _require_admin(x_admin_key)
    _require_kb_enabled()

    if get_running_kb_ingest_run():
        raise HTTPException(
            status_code=409,
            detail="A knowledge base ingest is already running.",
        )

    _schedule_ingest_job(
        scrape=False,
        force_reindex=True,
        brochure_only=False,
        brochure_html_path=None,
    )
    return {
        "status": "running",
        "message": "Re-index all started in background. Poll GET /kb/status for progress.",
    }


@router.get("/documents")
def kb_documents(source: str | None = None):
    if source and source not in ("external", "all"):
        raise HTTPException(
            status_code=400,
            detail="source must be 'external' or omitted",
        )
    filter_source = "external" if source == "external" else None
    docs = list_kb_documents(source=filter_source)
    return {"documents": docs, "count": len(docs)}


@router.post("/ingest/external")
def ingest_external(
    request: KbIngestRequest,
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    _require_admin(x_admin_key)
    _require_kb_enabled()

    if get_running_kb_ingest_run():
        raise HTTPException(
            status_code=409,
            detail="A knowledge base ingest is already running.",
        )

    brochure_html: Path | None = None
    if request.brochure_html:
        from app.config import BASE_DIR

        brochure_html = Path(request.brochure_html)
        if not brochure_html.is_absolute():
            brochure_html = BASE_DIR / brochure_html

    if request.background:
        _schedule_ingest_job(
            scrape=not request.no_scrape,
            force_reindex=request.force,
            brochure_only=request.brochure_only,
            brochure_html_path=brochure_html,
        )
        return {
            "status": "running",
            "message": "Ingest started in background. Poll GET /kb/status for progress.",
        }

    try:
        result = run_kb_pipeline(
            scrape=not request.no_scrape,
            ingest=True,
            brochure_only=request.brochure_only,
            brochure_html_path=brochure_html,
            force_reindex=request.force,
        )
        refresh_kb_document_cache()
        return {
            "run_id": result["run_id"],
            "status": result["status"],
            "scrape": result.get("scrape"),
            "documents_added": result.get("documents_added", 0),
            "chunks_added": result.get("chunks_added", 0),
            "ingest_count": result.get("ingest_count", 0),
        }
    except Exception as exc:
        logger.exception("KB ingest failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
