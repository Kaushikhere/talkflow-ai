import logging

from fastapi import APIRouter, HTTPException, Query

from app.database import get_uploaded_policy
from app.services.audit_compare import compare_policies

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["compare"])


@router.get("/compare")
def compare_uploaded_policies(
    policy_a: int = Query(..., ge=1, description="First policy ID"),
    policy_b: int = Query(..., ge=1, description="Second policy ID"),
):
    """Compare two audited policies and return an AI winner verdict."""
    if policy_a == policy_b:
        raise HTTPException(status_code=400, detail="Select two different policies to compare.")

    row_a = get_uploaded_policy(policy_a)
    row_b = get_uploaded_policy(policy_b)
    if not row_a:
        raise HTTPException(status_code=404, detail=f"Policy {policy_a} not found.")
    if not row_b:
        raise HTTPException(status_code=404, detail=f"Policy {policy_b} not found.")

    try:
        return compare_policies(row_a, row_b)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Policy comparison failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Comparison failed: {exc}") from exc
