import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse

from app.config import BASE_DIR, KB_DATA_DIR, KB_ENABLED, KB_RERANK_ENABLED, KB_RETRIEVE_POOL, KB_TOP_K
from app.database import (
    get_kb_document_by_id,
    get_kb_stats,
    get_last_kb_ingest_run,
    get_running_kb_ingest_run,
    list_kb_documents,
)
from app.models import KbIngestRequest, PolicyRecommendRequest
from app.services.document_extraction import extract_pdf_pages
from app.services.kb_ingest import run_kb_pipeline
from app.services.policy_recommend import recommend_policies

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kb", tags=["knowledge-base"])


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
    except Exception:
        logger.exception("Background KB ingest failed")


@router.get("/status")
def kb_status():
    stats = get_kb_stats()
    last_run = get_last_kb_ingest_run()
    running = get_running_kb_ingest_run()
    return {
        "enabled": KB_ENABLED,
        "top_k": KB_TOP_K,
        "retrieve_pool": KB_RETRIEVE_POOL,
        "rerank_enabled": KB_RERANK_ENABLED,
        "documents_total": stats["documents_total"],
        "documents_indexed": stats["documents_indexed"],
        "chunks_total": stats["chunks_total"],
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
    background_tasks: BackgroundTasks,
):
    if not KB_ENABLED:
        raise HTTPException(
            status_code=400,
            detail="Knowledge base is disabled (set KB_ENABLED=true in .env).",
        )

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
        background_tasks.add_task(
            _run_ingest_job,
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
