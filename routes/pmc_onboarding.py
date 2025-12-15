from __future__ import annotations

import os
import stripe
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from models import PMC, PMCIntegration, PMCUser, Property

# ✅ Import your sync function (you referenced it below)
from utils.pms_sync import sync_properties


# ✅ MUST exist before any @router usage
router = APIRouter()
templates = Jinja2Templates(directory="templates")

# ✅ MUST exist before any onboarding_hostaway_import usage
SUPPORTED_PROVIDERS = {"hostaway", "lodgify", "guesty"}


# ----------------------------
# Small helpers
# ----------------------------
def _set_if_attr(obj, attr: str, value) -> None:
    if hasattr(obj, attr):
        setattr(obj, attr, value)


# ----------------------------
# Session helpers
# ----------------------------
def _session_user(request: Request) -> dict:
    return request.session.get("user") or {}


def _session_email(request: Request) -> Optional[str]:
    email = (_session_user(request).get("email") or "").strip().lower()
    return email or None


def _redirect_to_google_login(next_path: str) -> RedirectResponse:
    return RedirectResponse(url=f"/auth/login/google?next={next_path}", status_code=302)


def _require_login_or_redirect(request: Request, next_path: str) -> Optional[RedirectResponse]:
    if not _session_email(request):
        return _redirect_to_google_login(next_path)
    return None


def _require_pmc_for_session(db: Session, request: Request) -> PMC:
    """
    Determine the PMC for the logged-in user.

    Resolution order:
    1) Active PMCUser membership (most explicit)
    2) Fallback to PMC.email (latest PMC wins)

    Always returns the MOST RECENT PMC to avoid stale onboarding data.
    """
    email_l = _session_email(request)
    if not email_l:
        raise HTTPException(status_code=403, detail="Not logged in")

    # 1️⃣ Preferred: PMCUser membership
    pmc_user = (
        db.query(PMCUser)
        .filter(
            func.lower(PMCUser.email) == email_l,
            PMCUser.is_active.is_(True),
        )
        .order_by(PMCUser.id.desc())
        .first()
    )

    if pmc_user:
        pmc = (
            db.query(PMC)
            .filter(PMC.id == pmc_user.pmc_id)
            .first()
        )
        if pmc:
            return pmc

    # 2️⃣ Fallback: PMC owner email
    pmc = (
        db.query(PMC)
        .filter(func.lower(PMC.email) == email_l)
        .order_by(PMC.id.desc())   # 🔑 CRITICAL FIX
        .first()
    )

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


def _render_pms_page(
    request: Request,
    pmc: PMC,
    existing: Optional[PMCIntegration],
    error: Optional[str] = None,
    provider: str = "hostaway",
):
    return templates.TemplateResponse(
        "pmc_onboarding_pms.html",
        {
            "request": request,
            "pmc": pmc,
            "existing": existing,
            "error": error,
            "provider": provider,
        },
    )


# ----------------------------
# GET: Connect PMS screen
# ----------------------------
@router.get("/pmc/onboarding/pms", response_class=HTMLResponse)
def onboarding_pms_page(request: Request, db: Session = Depends(get_db)):
    redirect = _require_login_or_redirect(request, "/pmc/onboarding/pms")
    if redirect:
        return redirect

    pmc = _require_pmc_for_session(db, request)

    if not _is_paid(pmc):
        return RedirectResponse("/pmc/signup", status_code=303)

    provider = "hostaway"
    existing = (
        db.query(PMCIntegration)
        .filter(PMCIntegration.pmc_id == pmc.id, PMCIntegration.provider == provider)
        .first()
    )

    return _render_pms_page(request, pmc, existing, provider=provider)


# ----------------------------
# POST: Save creds + import properties (Hostaway v1)
# ----------------------------
@router.post("/pmc/onboarding/pms/hostaway/import")
def onboarding_hostaway_import(
    request: Request,
    account_id: str = Form(...),
    api_secret: str = Form(...),  # Hostaway API key (treated as secret)
    db: Session = Depends(get_db),
):
    # Redirect if the session is not logged in or not permitted
    redirect = _require_login_or_redirect(request, "/pmc/onboarding/pms")
    if redirect:
        return redirect

    pmc = _require_pmc_for_session(db, request)
    if not _is_paid(pmc):
        return RedirectResponse("/pmc/signup", status_code=303)

    provider = "hostaway"
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=400, detail="Unsupported provider")

    account_id_clean = (account_id or "").strip()
    hostaway_api_key = (api_secret or "").strip()

    if not account_id_clean or not hostaway_api_key:
        raise HTTPException(status_code=400, detail="Missing Hostaway credentials")

    # 1) Upsert integration record
    integ = (
        db.query(PMCIntegration)
        .filter(PMCIntegration.pmc_id == pmc.id, PMCIntegration.provider == provider)
        .first()
    )
    if not integ:
        integ = PMCIntegration(pmc_id=pmc.id, provider=provider)
        db.add(integ)

    integ.account_id = account_id_clean
    integ.api_key = None                       # Hostaway: no separate client_id concept
    integ.api_secret = hostaway_api_key        # Hostaway API key stored here
    integ.is_connected = True
    integ.updated_at = datetime.utcnow()

    # 2) Mirror into PMC table
    _set_if_attr(pmc, "pms_integration", provider)
    _set_if_attr(pmc, "pms_account_id", account_id_clean)
    # For Hostaway, pms_api_key must equal pms_account_id
    _set_if_attr(pmc, "pms_api_key", account_id_clean)
    # Store the Hostaway API key in pms_api_secret
    _set_if_attr(pmc, "pms_api_secret", hostaway_api_key)

    db.commit()
    db.refresh(integ)
    db.refresh(pmc)

    # 3) Import properties
    try:
        sync_properties(account_id=str(account_id_clean))
    except Exception as e:
        return _render_pms_page(
            request,
            pmc,
            integ,
            error=f"Hostaway import failed: {str(e)}",
            provider=provider,
        )

    # Verify that properties were saved for this pmc.id
    imported_count = (
        db.query(Property)
        .filter(Property.pmc_id == pmc.id)
        .count()
    )

    print(
        "[hostaway_import] pmc_id=", pmc.id,
        "account_id=", account_id_clean,
        "imported_count=", imported_count
    )

    if imported_count == 0:
        return _render_pms_page(
            request,
            pmc,
            integ,
            error=(
                "Hostaway import completed but 0 properties were saved. "
                "This usually means your Account ID/API Key pair did not yield listings, "
                "or the sync wrote to a different PMC record."
            ),
            provider=provider,
        )

    # 4) Mark last sync timestamp if the column exists
    if hasattr(integ, "last_synced_at"):
        integ.last_synced_at = datetime.utcnow()
        db.commit()

    return RedirectResponse("/pmc/onboarding/properties", status_code=303)


# ----------------------------
# POST: Lodgify placeholder
# ----------------------------
@router.post("/pmc/onboarding/pms/lodgify/import")
def onboarding_lodgify_import(request: Request, db: Session = Depends(get_db)):
    redirect = _require_login_or_redirect(request, "/pmc/onboarding/pms")
    if redirect:
        return redirect

    pmc = _require_pmc_for_session(db, request)
    if not _is_paid(pmc):
        return RedirectResponse("/pmc/signup", status_code=303)

    raise HTTPException(status_code=501, detail="Lodgify integration is coming soon")


# ----------------------------
# POST: Guesty placeholder
# ----------------------------
@router.post("/pmc/onboarding/pms/guesty/import")
def onboarding_guesty_import(request: Request, db: Session = Depends(get_db)):
    redirect = _require_login_or_redirect(request, "/pmc/onboarding/pms")
    if redirect:
        return redirect

    pmc = _require_pmc_for_session(db, request)
    if not _is_paid(pmc):
        return RedirectResponse("/pmc/signup", status_code=303)

    raise HTTPException(status_code=501, detail="Guesty integration is coming soon")


# ----------------------------
# GET: Choose properties after import
# ----------------------------
@router.get("/pmc/onboarding/properties", response_class=HTMLResponse)
def onboarding_properties_page(request: Request, db: Session = Depends(get_db)):
    redirect = _require_login_or_redirect(request, "/pmc/onboarding/properties")
    if redirect:
        return redirect

    pmc = _require_pmc_for_session(db, request)
    if not _is_paid(pmc):
        return RedirectResponse("/pmc/signup", status_code=303)

    properties = (
        db.query(Property)
        .filter(Property.pmc_id == pmc.id)
        .order_by(Property.property_name)
        .all()
    )

    return templates.TemplateResponse(
        "pmc_onboarding_properties.html",
        {"request": request, "pmc": pmc, "properties": properties},
    )


# ----------------------------
# POST: Save property choices + start subscription checkout if needed
# ----------------------------
@router.post("/pmc/onboarding/properties")
async def onboarding_properties_submit(request: Request, db: Session = Depends(get_db)):
    redirect = _require_login_or_redirect(request, "/pmc/onboarding/properties")
    if redirect:
        return redirect

    pmc = _require_pmc_for_session(db, request)
    if not _is_paid(pmc):
        return RedirectResponse("/pmc/signup", status_code=303)

    form = await request.form()

    selected_ids = set()
    for pid in form.getlist("property_ids"):
        try:
            selected_ids.add(int(pid))
        except (ValueError, TypeError):
            continue

    props = db.query(Property).filter(Property.pmc_id == pmc.id).all()
    for prop in props:
        prop.sandy_enabled = prop.id in selected_ids
    db.commit()

    enabled_count = len(selected_ids)
    if enabled_count <= 0:
        return RedirectResponse("/admin/dashboard#properties", status_code=303)

    customer_id = getattr(pmc, "stripe_customer_id", None)
    if not customer_id:
        return RedirectResponse("/pmc/signup", status_code=303)

    stripe_secret = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    price_monthly = (os.getenv("STRIPE_PRICE_PROPERTY_MONTHLY") or "").strip()
    app_base = (os.getenv("APP_BASE_URL") or "").rstrip("/")

    if not stripe_secret or not price_monthly or not app_base:
        raise HTTPException(status_code=500, detail="Missing Stripe env vars for subscription checkout")

    stripe.api_key = stripe_secret

    checkout = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_monthly, "quantity": enabled_count}],
        success_url=f"{app_base}/pmc/onboarding/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{app_base}/pmc/onboarding/properties",
        metadata={
            "pmc_id": str(pmc.id),
            "type": "pmc_property_subscription",
            "quantity": str(enabled_count),
        },
    )

    return RedirectResponse(checkout.url, status_code=303)


# ----------------------------
# GET: Subscription checkout success
# ----------------------------
@router.get("/pmc/onboarding/billing/success")
def onboarding_billing_success(
    request: Request,
    session_id: str = Query(...),
    db: Session = Depends(get_db),
):
    redirect = _require_login_or_redirect(request, "/pmc/onboarding/billing/success")
    if redirect:
        return redirect

    pmc = _require_pmc_for_session(db, request)
    if not _is_paid(pmc):
        return RedirectResponse("/pmc/signup", status_code=303)

    stripe_secret = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    if not stripe_secret:
        raise HTTPException(status_code=500, detail="Missing STRIPE_SECRET_KEY")

    stripe.api_key = stripe_secret

    try:
        cs = stripe.checkout.Session.retrieve(session_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid Stripe session_id: {str(e)}")

    metadata = cs.get("metadata") or {}
    cs_type = (metadata.get("type") or "").strip()
    cs_pmc_id = (metadata.get("pmc_id") or "").strip()

    if cs_type and cs_type != "pmc_property_subscription":
        return RedirectResponse("/admin/dashboard#properties", status_code=303)

    if cs_pmc_id and cs_pmc_id != str(pmc.id):
        raise HTTPException(status_code=403, detail="Checkout session does not match your account")

    subscription_id = cs.get("subscription")
    customer_id = cs.get("customer")

    if subscription_id and hasattr(pmc, "stripe_subscription_id"):
        pmc.stripe_subscription_id = subscription_id

    if customer_id and not getattr(pmc, "stripe_customer_id", None) and hasattr(pmc, "stripe_customer_id"):
        pmc.stripe_customer_id = customer_id

    _set_if_attr(pmc, "last_stripe_checkout_session_id", cs.get("id"))
    db.commit()

    return RedirectResponse("/admin/dashboard#properties", status_code=303)
