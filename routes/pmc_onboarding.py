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

from utils.pms_sync import sync_properties

router = APIRouter()
templates = Jinja2Templates(directory="templates")

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
    """
    email_l = _session_email(request)
    if not email_l:
        raise HTTPException(status_code=403, detail="Not logged in")

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
        pmc = db.query(PMC).filter(PMC.id == pmc_user.pmc_id).first()
        if pmc:
            return pmc

    pmc = (
        db.query(PMC)
        .filter(func.lower(PMC.email) == email_l)
        .order_by(PMC.id.desc())
        .first()
    )
    if pmc:
        return pmc

    raise HTTPException(status_code=403, detail="No PMC found for this login")


# ----------------------------
# GET: Connect PMS screen
# ----------------------------
@router.get("/pmc/onboarding/pms", response_class=HTMLResponse)
def onboarding_pms_page(request: Request, db: Session = Depends(get_db)):
    """
    New flow: allow PMS connect BEFORE Stripe payment.
    """
    redirect = _require_login_or_redirect(request, "/pmc/onboarding/pms")
    if redirect:
        return redirect

    pmc = _require_pmc_for_session(db, request)

    provider = "hostaway"
    existing = (
        db.query(PMCIntegration)
        .filter(PMCIntegration.pmc_id == pmc.id, PMCIntegration.provider == provider)
        .first()
    )

    return templates.TemplateResponse(
        "pmc_onboarding_pms.html",
        {"request": request, "pmc": pmc, "existing": existing, "error": None, "provider": provider},
    )


# ----------------------------
# POST: Save creds + import properties (Hostaway v1)
# ----------------------------
@router.post("/pmc/onboarding/pms/hostaway/import")
def onboarding_hostaway_import(
    request: Request,
    account_id: str = Form(...),
    api_secret: str = Form(...),  # Hostaway API key
    db: Session = Depends(get_db),
):
    """
    New flow: allow import BEFORE Stripe payment.
    """
    redirect = _require_login_or_redirect(request, "/pmc/onboarding/pms")
    if redirect:
        return redirect

    pmc = _require_pmc_for_session(db, request)

    provider = "hostaway"
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=400, detail="Unsupported provider")

    account_id_clean = (account_id or "").strip()
    hostaway_api_key = (api_secret or "").strip()
    if not account_id_clean or not hostaway_api_key:
        raise HTTPException(status_code=400, detail="Missing Hostaway credentials")

    integ = (
        db.query(PMCIntegration)
        .filter(PMCIntegration.pmc_id == pmc.id, PMCIntegration.provider == provider)
        .first()
    )
    if not integ:
        integ = PMCIntegration(pmc_id=pmc.id, provider=provider)
        db.add(integ)

    integ.account_id = account_id_clean
    integ.api_key = None
    integ.api_secret = hostaway_api_key
    integ.is_connected = True
    integ.updated_at = datetime.utcnow()

    # Hostaway mapping:
    # - pms_account_id = account id (used for syncing)
    # - pms_api_key     = MUST equal account id (per your requirement)
    # - pms_api_secret  = hostaway API key
    _set_if_attr(pmc, "pms_integration", provider)
    _set_if_attr(pmc, "pms_account_id", account_id_clean)
    _set_if_attr(pmc, "pms_api_key", account_id_clean)
    _set_if_attr(pmc, "pms_api_secret", hostaway_api_key)

    db.commit()
    db.refresh(integ)
    db.refresh(pmc)

    try:
        sync_properties(account_id=str(account_id_clean))
    except Exception as e:
        return templates.TemplateResponse(
            "pmc_onboarding_pms.html",
            {"request": request, "pmc": pmc, "existing": integ, "error": f"Hostaway import failed: {str(e)}", "provider": provider},
        )

    imported_count = db.query(Property).filter(Property.pmc_id == pmc.id).count()
    print("[hostaway_import] pmc_id=", pmc.id, "account_id=", account_id_clean, "imported_count=", imported_count)

    if imported_count == 0:
        return templates.TemplateResponse(
            "pmc_onboarding_pms.html",
            {
                "request": request,
                "pmc": pmc,
                "existing": integ,
                "error": (
                    "Hostaway import completed but no properties were saved. "
                    "Check Account ID + API key, or ensure sync writes to this PMC."
                ),
                "provider": provider,
            },
        )

    if hasattr(integ, "last_synced_at"):
        integ.last_synced_at = datetime.utcnow()
        db.commit()

    return RedirectResponse("/pmc/onboarding/properties", status_code=303)


# ----------------------------
# GET: Choose properties
# ----------------------------
@router.get("/pmc/onboarding/properties", response_class=HTMLResponse)
def onboarding_properties_page(request: Request, db: Session = Depends(get_db)):
    redirect = _require_login_or_redirect(request, "/pmc/onboarding/properties")
    if redirect:
        return redirect

    pmc = _require_pmc_for_session(db, request)

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
# POST: Save property choices -> go to billing review (NO STRIPE HERE)
# ----------------------------
@router.post("/pmc/onboarding/properties")
async def onboarding_properties_submit(request: Request, db: Session = Depends(get_db)):
    redirect = _require_login_or_redirect(request, "/pmc/onboarding/properties")
    if redirect:
        return redirect

    pmc = _require_pmc_for_session(db, request)

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

    # Next step: billing breakdown + pay
    return RedirectResponse("/pmc/onboarding/billing-review", status_code=303)


# ----------------------------
# POST: Save property choices -> go to billing review (NO STRIPE HERE)
# ----------------------------
@router.post("/pmc/onboarding/properties")
async def onboarding_properties_submit(request: Request, db: Session = Depends(get_db)):
    redirect = _require_login_or_redirect(request, "/pmc/onboarding/properties")
    if redirect:
        return redirect

    # If they somehow don't have a PMC yet, send them to create one
    try:
        pmc = _require_pmc_for_session(db, request)
    except HTTPException:
        return RedirectResponse("/pmc/signup", status_code=303)

    form = await request.form()

    selected_ids: set[int] = set()
    for pid in form.getlist("property_ids"):
        try:
            selected_ids.add(int(pid))
        except (ValueError, TypeError):
            continue

    # Update enabled flags for this PMC
    props = db.query(Property).filter(Property.pmc_id == pmc.id).all()
    for prop in props:
        prop.sandy_enabled = (prop.id in selected_ids)

    db.commit()

    # Next step: billing breakdown + pay
    return RedirectResponse("/pmc/onboarding/billing-review", status_code=303)


# ----------------------------
# GET: Billing review screen (NEW)
# ----------------------------
@router.get("/pmc/onboarding/billing-review", response_class=HTMLResponse)
def onboarding_billing_review(request: Request, db: Session = Depends(get_db)):
    redirect = _require_login_or_redirect(request, "/pmc/onboarding/billing-review")
    if redirect:
        return redirect

    # If they somehow don't have a PMC yet, send them to create one
    try:
        pmc = _require_pmc_for_session(db, request)
    except HTTPException:
        return RedirectResponse("/pmc/signup", status_code=303)

    enabled_count = (
        db.query(Property)
        .filter(Property.pmc_id == pmc.id, Property.sandy_enabled.is_(True))
        .count()
    )

    # Constants for display (Stripe will be created on NEXT step, not here)
    setup_fee_cents = 49900          # $499.00
    monthly_cents_each = 999         # $9.99 / property / month
    monthly_total_cents = enabled_count * monthly_cents_each

    return templates.TemplateResponse(
        "pmc_onboarding_billing_review.html",
        {
            "request": request,
            "pmc": pmc,
            "enabled_count": enabled_count,
            "setup_fee_cents": setup_fee_cents,
            "monthly_cents_each": monthly_cents_each,
            "monthly_total_cents": monthly_total_cents,
        },
    )



# ----------------------------
# POST: Create ONE Stripe checkout (setup fee + subscription) (NEW)
# ----------------------------
@router.post("/pmc/onboarding/billing/checkout")
def onboarding_billing_checkout(request: Request, db: Session = Depends(get_db)):
    redirect = _require_login_or_redirect(request, "/pmc/onboarding/billing-review")
    if redirect:
        return redirect

    pmc = _require_pmc_for_session(db, request)

    enabled_count = (
        db.query(Property)
        .filter(Property.pmc_id == pmc.id, Property.sandy_enabled.is_(True))
        .count()
    )

    if enabled_count <= 0:
        return RedirectResponse("/pmc/onboarding/properties", status_code=303)

    stripe_secret = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    app_base = (os.getenv("APP_BASE_URL") or "").rstrip("/")

    price_setup = (os.getenv("STRIPE_PRICE_SETUP_ONETIME") or "").strip()  # $499 one-time price ID
    price_monthly = (os.getenv("STRIPE_PRICE_PROPERTY_MONTHLY") or "").strip()  # $9.99/mo price ID

    if not stripe_secret or not app_base or not price_setup or not price_monthly:
        raise HTTPException(status_code=500, detail="Missing Stripe env vars (STRIPE_SECRET_KEY, APP_BASE_URL, STRIPE_PRICE_SETUP_ONETIME, STRIPE_PRICE_PROPERTY_MONTHLY)")

    stripe.api_key = stripe_secret

    email_l = _session_email(request)
    customer_id = getattr(pmc, "stripe_customer_id", None)

    # One checkout session that includes BOTH:
    # - setup fee (one-time)
    # - subscription monthly per property (quantity = enabled_count)
    checkout_kwargs = dict(
        mode="subscription",
        line_items=[
            {"price": price_setup, "quantity": 1},
            {"price": price_monthly, "quantity": int(enabled_count)},
        ],
        success_url=f"{app_base}/pmc/onboarding/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{app_base}/pmc/onboarding/billing-review",
        metadata={
            "pmc_id": str(pmc.id),
            "type": "pmc_full_activation",
            "quantity": str(enabled_count),
        },
    )

    # Prefer existing customer; otherwise create one during Checkout
    if customer_id:
        checkout_kwargs["customer"] = customer_id
    else:
        checkout_kwargs["customer_email"] = email_l or pmc.email
        checkout_kwargs["customer_creation"] = "always"

    checkout = stripe.checkout.Session.create(**checkout_kwargs)
    return RedirectResponse(checkout.url, status_code=303)


# ----------------------------
# GET: Checkout success -> activate + store ids, then dashboard
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

    if cs_type and cs_type != "pmc_full_activation":
        return RedirectResponse("/admin/dashboard#properties", status_code=303)

    if cs_pmc_id and cs_pmc_id != str(pmc.id):
        raise HTTPException(status_code=403, detail="Checkout session does not match your account")

    # If Checkout isn't complete/paid yet, just send them to review
    cs_status = (cs.get("status") or "").strip().lower()
    payment_status = (cs.get("payment_status") or "").strip().lower()
    if cs_status not in {"complete"} or payment_status not in {"paid", "no_payment_required"}:
        return RedirectResponse("/pmc/onboarding/billing-review", status_code=303)

    subscription_id = cs.get("subscription")
    customer_id = cs.get("customer")

    if customer_id:
        _set_if_attr(pmc, "stripe_customer_id", customer_id)
    if subscription_id:
        _set_if_attr(pmc, "stripe_subscription_id", subscription_id)

    _set_if_attr(pmc, "last_stripe_checkout_session_id", cs.get("id"))

    # ✅ Activate the account now (webhook still can backfill; this makes UX immediate)
    _set_if_attr(pmc, "billing_status", "active")
    _set_if_attr(pmc, "active", True)
    _set_if_attr(pmc, "sync_enabled", True)
    _set_if_attr(pmc, "signup_paid_at", datetime.utcnow())

    db.commit()

    return RedirectResponse("/admin/dashboard#properties", status_code=303)
