# routes/pmc_onboarding.py
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from models import PMC, PMCIntegration, PMCUser

# IMPORTANT: this should match your actual module path
# If your sync file is at project root: from pms_sync import sync_properties
# If it's in utils/: from utils.pms_sync import sync_properties
from pms_sync import sync_properties  # <-- adjust if needed

router = APIRouter()
templates = Jinja2Templates(directory="templates")


# ----------------------------
# Session helpers
# ----------------------------
def _session_email(request: Request) -> Optional[str]:
    user = request.session.get("user") or {}
    email = (user.get("email") or "").strip().lower()
    return email or None


def _require_login_or_redirect(request: Request, next_path: str) -> Optional[RedirectResponse]:
    if not _session_email(request):
        # preserve intended destination
        return RedirectResponse(f"/auth/login/google?next={next_path}", status_code=302)
    return None


def _require_pmc_for_session(db: Session, request: Request) -> PMC:
    """
    Determine the PMC for the logged-in user. Supports:
    - PMCUser membership
    - PMC.email fallback
    """
    email_l = _session_email(request)
    if not email_l:
        raise HTTPException(status_code=403, detail="Not logged in")

    pmc_user = (
        db.query(PMCUser)
        .filter(func.lower(PMCUser.email) == email_l, PMCUser.is_active.is_(True))
        .first()
    )
    if pmc_user:
        pmc = db.query(PMC).filter(PMC.id == pmc_user.pmc_id).first()
        if pmc:
            return pmc

    pmc = db.query(PMC).filter(func.lower(PMC.email) == email_l).first()
    if pmc:
        return pmc

    raise HTTPException(status_code=403, detail="No PMC found for this login")


def _is_paid(pmc: PMC) -> bool:
    """
    Treat webhook as truth:
    - billing_status == 'active' means paid
    - fallback: pmc.active True (legacy)
    """
    status = (getattr(pmc, "billing_status", "") or "").strip().lower()
    if status:
        return status == "active"
    return bool(getattr(pmc, "active", False))


def _render_pms_page(request: Request, pmc: PMC, existing: Optional[PMCIntegration], error: Optional[str] = None):
    return templates.TemplateResponse(
        "pmc_onboarding_pms.html",
        {
            "request": request,
            "pmc": pmc,
            "existing": existing,
            "error": error,
        },
    )


# ----------------------------
# Step 1: Connect PMS screen
# ----------------------------
@router.get("/pmc/onboarding/pms", response_class=HTMLResponse)
def onboarding_pms_page(request: Request, db: Session = Depends(get_db)):
    redirect = _require_login_or_redirect(request, "/pmc/onboarding/pms")
    if redirect:
        return redirect

    pmc = _require_pmc_for_session(db, request)

    if not _is_paid(pmc):
        # Guard: don’t allow onboarding if they didn’t pay
        return RedirectResponse("/pmc/signup", status_code=303)

    existing = (
        db.query(PMCIntegration)
        .filter(PMCIntegration.pmc_id == pmc.id, PMCIntegration.provider == "hostaway")
        .first()
    )

    return _render_pms_page(request, pmc, existing)


# ----------------------------
# Step 2: Save creds + import properties (Hostaway v1)
# ----------------------------
@router.post("/pmc/onboarding/pms/hostaway/import")
def onboarding_hostaway_import(
    request: Request,
    account_id: str = Form(...),
    api_key: str = Form(...),
    api_secret: str = Form(...),
    db: Session = Depends(get_db),
):
    redirect = _require_login_or_redirect(request, "/pmc/onboarding/pms")
    if redirect:
        return redirect

    pmc = _require_pmc_for_session(db, request)

    if not _is_paid(pmc):
        return RedirectResponse("/pmc/signup", status_code=303)

    account_id = (account_id or "").strip()
    api_key = (api_key or "").strip()
    api_secret = (api_secret or "").strip()

    if not account_id or not api_key or not api_secret:
        raise HTTPException(status_code=400, detail="Missing Hostaway credentials")

    # 1) Upsert provider-agnostic integration record
    integ = (
        db.query(PMCIntegration)
        .filter(PMCIntegration.pmc_id == pmc.id, PMCIntegration.provider == "hostaway")
        .first()
    )
    if not integ:
        integ = PMCIntegration(pmc_id=pmc.id, provider="hostaway")
        db.add(integ)

    integ.account_id = account_id
    integ.api_key = api_key
    integ.api_secret = api_secret
    integ.is_connected = True
    integ.updated_at = datetime.utcnow()

    # 2) Mirror into PMC table too (keeps your existing app logic working)
    # (You already have these columns on PMC)
    pmc.pms_integration = "hostaway"
    pmc.pms_account_id = account_id
    pmc.pms_api_key = api_key
    pmc.pms_api_secret = api_secret

    db.commit()
    db.refresh(integ)
    db.refresh(pmc)

    # 3) Import properties using your existing sync util
    # This also acts as the connectivity test.
    try:
        count = sync_properties(account_id=str(account_id))
    except Exception as e:
        # Render the page with the error, keep creds saved (so they can retry)
        existing = integ
        return _render_pms_page(
            request,
            pmc,
            existing,
            error=f"Hostaway import failed: {str(e)}",
        )

    # Track sync time on integration (optional but nice)
    if hasattr(integ, "last_synced_at"):
        integ.last_synced_at = datetime.utcnow()
        db.commit()

    return RedirectResponse("/admin/dashboard", status_code=303)
