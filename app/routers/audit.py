import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse

from app.config import AUDIT_UPLOADS_DIR, MAX_UPLOAD_BYTES
from app.database import delete_uploaded_policy, get_uploaded_policy, list_uploaded_policies
from app.models import AuditChatRequest
from app.services.audit_chat import generate_audit_chat_reply, stream_audit_chat_events
from app.services.audit_market_data import resolve_evaluation_profile
from app.services.audit_pipeline import get_policy_source, policy_to_response, run_audit_pipeline
from app.services.audit_report import build_markdown_report, build_pdf_report, report_attachment_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audit", tags=["audit"])

UPLOAD_CHUNK_SIZE = 1024 * 1024


def _resolve_audit_pdf_path(stored_path: str) -> Path:
    path = Path(stored_path).resolve()
    audit_root = AUDIT_UPLOADS_DIR.resolve()
    try:
        path.relative_to(audit_root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Invalid policy file path") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Policy PDF file not found")
    return path


@router.get("/pincode/{pincode}")
def audit_pincode_lookup(pincode: str):
    """Resolve an Indian pincode to district/city (India Post reference data)."""
    from app.services.audit_pincode import lookup_india_pincode

    result = lookup_india_pincode(pincode)
    if not result.get("valid"):
        raise HTTPException(status_code=404, detail="Invalid or unknown Indian pincode.")
    return result


@router.get("/market-benchmarks")
def audit_market_benchmarks():
    """Geo benchmarks are resolved per policy by the analysis model (not a fixed city list)."""
    return {
        "mode": "llm",
        "description": (
            "City tier, local hospital room costs, and minimum sum insured are determined per upload "
            "from the policy pincode, India Post facts, and policy text via the geographic analysis model."
        ),
    }


@router.post("/upload")
async def audit_upload(file: UploadFile = File(...)):
    """Upload a policy PDF; city/zone are read from the document for benchmarking."""
    safe_name = Path(file.filename or "policy.pdf").name
    if Path(safe_name).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are supported for audit.")

    stored_name = f"{uuid.uuid4().hex}.pdf"
    destination = AUDIT_UPLOADS_DIR / stored_name
    size = 0

    try:
        with destination.open("wb") as output:
            while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    destination.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=400,
                        detail=f"File too large. Maximum size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                    )
                output.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        destination.unlink(missing_ok=True)
        logger.error("Audit upload write failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to save uploaded file.") from exc
    finally:
        await file.close()

    try:
        result = run_audit_pipeline(destination, safe_name)
        return result
    except ValueError as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Audit pipeline failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Audit pipeline failed: {exc}",
        ) from exc


@router.get("/policies")
def audit_list_policies():
    """List recent uploaded policies for Audit mode history."""
    return {"policies": list_uploaded_policies()}


@router.get("/policies/{policy_id}")
def audit_get_policy(policy_id: int):
    """Get scorecard and verdict for a previously audited policy."""
    policy = get_uploaded_policy(policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy_to_response(policy)


@router.delete("/policies/{policy_id}")
def audit_delete_policy(policy_id: int):
    """Remove an audited policy from history and delete its stored PDF."""
    if not get_uploaded_policy(policy_id):
        raise HTTPException(status_code=404, detail="Policy not found")
    if not delete_uploaded_policy(policy_id):
        raise HTTPException(status_code=404, detail="Policy not found")
    return {"ok": True, "policy_id": policy_id}


@router.get("/policies/{policy_id}/file")
def audit_download_original(policy_id: int):
    """Download the original uploaded policy PDF."""
    policy = get_uploaded_policy(policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    pdf_path = _resolve_audit_pdf_path(policy["stored_path"])
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=policy.get("filename") or pdf_path.name,
    )


@router.get("/policies/{policy_id}/source/{source_key}")
def audit_get_source(policy_id: int, source_key: str):
    """Get source excerpt for a metric or risk/strength item."""
    policy = get_uploaded_policy(policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    try:
        source = get_policy_source(policy, source_key)
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=400, detail="Invalid source key") from exc
    if not source:
        raise HTTPException(status_code=404, detail="Source excerpt not found")
    return source


@router.get("/policies/{policy_id}/export")
def audit_export_report(
    policy_id: int,
    format: str = Query(default="markdown", pattern="^(markdown|pdf)$"),
):
    """Export audit scorecard and verdict as Markdown or PDF."""
    policy = get_uploaded_policy(policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    filename = report_attachment_name(policy, format)

    if format == "pdf":
        content = build_pdf_report(policy)
        return Response(
            content=content,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    markdown = build_markdown_report(policy)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/policies/{policy_id}/chat")
def audit_policy_chat(policy_id: int, request: AuditChatRequest):
    """Follow-up Q&A grounded only in the uploaded policy."""
    if not get_uploaded_policy(policy_id):
        raise HTTPException(status_code=404, detail="Policy not found")

    if request.stream:
        return StreamingResponse(
            stream_audit_chat_events(policy_id, request.message),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        return generate_audit_chat_reply(policy_id, request.message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Audit chat failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
