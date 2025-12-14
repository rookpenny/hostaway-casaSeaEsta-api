import os
import requests
from datetime import datetime
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from models import PMC, PMCIntegration, Property, PMCUser

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _session_email(request: Request) -> str | None:
    user = request.session.get("user") or {}
    email = (user.get("email") or "").strip().lower()
    return email or None


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
        .filter(func.lower(PMCUser.email) == email_l, PMCUser.is_active == True)
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
    # Use billing_status if present, otherwise fall back to active
    status = (getattr(pmc, "billing_status", "") or "").lower()
    if status:
        return status == "active"
    return bool(getattr(pmc, "active", False))


# ----------------------------
# Step 1: Redirect success -> onboarding
# ----------------------------



# ----------------------------
# Step 2: Connect PMS screen
# ----------------------------
@router.get("/pmc/onboarding/pms", response_class=HTMLResponse)
def onboarding_pms_page(request: Request, db: Session = Depends(get_db)):
    pmc = _require_pmc_for_session(db, request)

    if not _is_paid(pmc):
        # Guard: don’t allow onboarding if they didn’t pay
        return RedirectResponse("/pmc/signup", status_code=303)

    # prefill any existing integration
    existing = (
        db.query(PMCIntegration)
        .filter(PMCIntegration.pmc_id == pmc.id, PMCIntegration.provider == "hostaway")
        .first()
    )

    return templates.TemplateResponse(
        "pmc_onboarding_pms.html",
        {
            "request": request,
            "pmc": pmc,
            "existing": existing,
        },
    )


# ----------------------------
# Step 3: Test + Save + Import properties (Hostaway v1)
# ----------------------------
@router.post("/pmc/onboarding/pms/hostaway/import")
def onboarding_hostaway_import(
    request: Request,
    account_id: str = Form(...),
    api_key: str = Form(...),
    api_secret: str = Form(...),
    db: Session = Depends(get_db),
):
    pmc = _require_pmc_for_session(db, request)

    if not _is_paid(pmc):
        return RedirectResponse("/pmc/signup", status_code=303)

    account_id = (account_id or "").strip()
    api_key = (api_key or "").strip()
    api_secret = (api_secret or "").strip()

    if not account_id or not api_key or not api_secret:
        raise HTTPException(status_code=400, detail="Missing Hostaway credentials")

    # 1) Save/update integration record (provider-agnostic design)
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
    db.commit()
    db.refresh(integ)

    # 2) Test connection + list properties from Hostaway
    # NOTE: You likely already have Hostaway helpers. If so, swap this block to your existing client.
    try:
        # Example endpoint pattern varies by Hostaway setup;
        # replace with your known-good Hostaway call used in utils.hostaway.
        # We’ll do a “minimal connectivity check” by calling an endpoint you already use.
        #
        # ✅ BEST: reuse your get_listing_overview / get_upcoming_phone_for_listing auth approach.
        #
        # For now, we’ll keep this as a placeholder and rely on your existing sync util.
        pass
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Hostaway connection failed: {str(e)}")

    # 3) Import properties using YOUR existing sync util
    # If you already have sync_properties(db, pmc_id) you can call it here.
    # If you have sync_properties(pmc_id) without db, call that.
    from utils.pms_sync import sync_properties  # adjust import to your real function

    try:
        # Expectation: sync_properties should upsert properties for THIS pmc
        # and set property.pms_integration/provider fields.
        count = sync_properties(db=db, pmc_id=pmc.id, provider="hostaway")
    except TypeError:
        # fallback to older signature (if your function doesn't accept provider yet)
        count = sync_properties(db=db, pmc_id=pmc.id)

    integ.last_synced_at = datetime.utcnow()
    db.commit()

    return RedirectResponse("/admin/dashboard", status_code=303)
