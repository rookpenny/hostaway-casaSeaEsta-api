from __future__ import annotations

import os
import stripe
from datetime import datetime, timezone 
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

# Supported PMS providers (only Hostaway is live right now)
SUPPORTED_PROVIDERS = {"hostaway", "lodgify", "guesty"}


# ----------------------------
# Small helpers
# ----------------------------
def _set_if_attr(obj, attr: str, value) -> None:
    """Set attr on obj only if the attribute exists (avoids migration issues)."""
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
    """Build a Google-login redirect preserving the desired post-login URL."""
    return RedirectResponse(url=f"/auth/login/google?next={next_path}", status_code=302)


def _require_login_or_redirect(request: Request, next_path: str) -> Optional[RedirectResponse]:
    """Return a RedirectResponse if the user is not logged in."""
    if not _session_email(request):
        return _redirect_to_google_login(next_path)
    return None


def _require_pmc_for_session(db: Session, request: Request) -> PMC:
    """
    Find the PMC associated with the current user.
    Resolution priority:
    1. Active PMCUser record
    2. Fall back to the latest PMC with matching email
    """
    email_l = _session_email(request)
    if not email_l:
        raise HTTPException(status_code=403, detail="Not logged in")

    # check for active PMCUser membership
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

    # fallback: latest PMC record with matching email
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
    Display PMS connection screen.  In the new flow, we do NOT block unpaid users here.
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
# POST: Save Hostaway creds and import properties
# ----------------------------
@router.post("/pmc/onboarding/pms/hostaway/import")
def onboarding_hostaway_import(
    request: Request,
    account_id: str = Form(...),
    api_secret: str = Form(...),  # Hostaway API key
    db: Session = Depends(get_db),
):
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

    # Upsert integration row
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

    # Optional legacy mirror into PMC (safe if columns exist)
    _set_if_attr(pmc, "pms_integration", provider)
    _set_if_attr(pmc, "pms_account_id", account_id_clean)
    _set_if_attr(pmc, "pms_api_key", account_id_clean)
    _set_if_attr(pmc, "pms_api_secret", hostaway_api_key)

    db.commit()
    db.refresh(integ)
    db.refresh(pmc)

    # ✅ Import properties immediately (THIS WAS MISSING)
    try:
        sync_properties(integ.id)
    except Exception as e:
        return templates.TemplateResponse(
            "pmc_onboarding_pms.html",
            {
                "request": request,
                "pmc": pmc,
                "existing": integ,
                "error": f"Hostaway import failed: {str(e)}",
                "provider": provider,
            },
        )

    # Ensure this session sees new rows (sync uses separate Session/engine)
    db.expire_all()

    imported_count = (
        db.query(Property)
        .filter(Property.integration_id == integ.id)
        .count()
    )

    print(
        "[hostaway_import] pmc_id=", pmc.id,
        "integration_id=", integ.id,
        "account_id=", account_id_clean,
        "imported_count=", imported_count
    )

    if imported_count == 0:
        return templates.TemplateResponse(
            "pmc_onboarding_pms.html",
            {
                "request": request,
                "pmc": pmc,
                "existing": integ,
                "error": (
                    "Hostaway import completed but no properties were saved. "
                    "Check your Account ID and API key."
                ),
                "provider": provider,
            },
        )

    # Mark last_synced_at after successful import
    if hasattr(integ, "last_synced_at"):
        integ.last_synced_at = datetime.utcnow()
        db.commit()

    return RedirectResponse("/pmc/onboarding/properties", status_code=303)

# ----------------------------
# GET: Choose properties to enable
# ----------------------------
@router.get("/pmc/onboarding/properties", response_class=HTMLResponse)
def onboarding_properties_page(request: Request, db: Session = Depends(get_db)):
    """
    Show imported properties; user selects which ones to enable Sandy for.
    """
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
# POST: Save property choices and go to billing review
# ----------------------------
@router.post("/pmc/onboarding/properties")
async def onboarding_properties_submit(request: Request, db: Session = Depends(get_db)):
    redirect = _require_login_or_redirect(request, "/pmc/onboarding/properties")
    if redirect:
        return redirect

    # If no PMC exists (unlikely), send the user to create one
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

    # Mark enabled properties
    props = db.query(Property).filter(Property.pmc_id == pmc.id).all()
    for prop in props:
        prop.sandy_enabled = (prop.id in selected_ids)

    db.commit()

    # Go to the billing-review page (no Stripe here yet)
    return RedirectResponse("/pmc/onboarding/billing-review", status_code=303)


# ----------------------------
# GET: Billing review screen
# ----------------------------
@router.get("/pmc/onboarding/billing-review", response_class=HTMLResponse)
def onboarding_billing_review(request: Request, db: Session = Depends(get_db)):
    """
    Display breakdown of one-time setup fee and monthly per-property fees.
    """
    redirect = _require_login_or_redirect(request, "/pmc/onboarding/billing-review")
    if redirect:
        return redirect

    # Ensure a PMC exists; otherwise redirect to signup
    try:
        pmc = _require_pmc_for_session(db, request)
    except HTTPException:
        return RedirectResponse("/pmc/signup", status_code=303)

    # Count how many properties were enabled
    enabled_count = (
        db.query(Property)
        .filter(Property.pmc_id == pmc.id, Property.sandy_enabled.is_(True))
        .count()
    )

    # Prices in cents (adjust as needed)
    setup_fee_cents = 49900       # $499.00 one-time
    monthly_cents_each = 999      # $9.99 per property per month
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
# POST: Create one Stripe checkout (setup fee + subscription)
# ----------------------------
@router.post("/pmc/onboarding/billing/checkout")
def onboarding_billing_checkout(request: Request, db: Session = Depends(get_db)):
    """
    Build a single Stripe Checkout that includes both the one-time setup fee and
    the monthly subscription for enabled properties.
    """
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

    # Load env variables
    stripe_secret = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    app_base = (os.getenv("APP_BASE_URL") or "").rstrip("/")
    price_setup = (os.getenv("STRIPE_PRICE_SETUP_ONETIME") or "").strip()        # One-time price ID
    price_monthly = (os.getenv("STRIPE_PRICE_PROPERTY_MONTHLY") or "").strip()  # Monthly price ID

    if not stripe_secret or not app_base or not price_setup or not price_monthly:
        raise HTTPException(status_code=500, detail="Missing Stripe environment variables")

    stripe.api_key = stripe_secret

    email_l = _session_email(request)
    customer_id = getattr(pmc, "stripe_customer_id", None)

    # Build a subscription-mode checkout with two line items:
    # 1. Setup fee, quantity 1 (one-time)
    # 2. Monthly per-property, quantity = enabled_count (recurring)
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

    # If a customer ID exists, reuse it; otherwise let Stripe create a customer
    if customer_id:
        checkout_kwargs["customer"] = customer_id
    else:
        checkout_kwargs["customer_email"] = email_l or pmc.email
        checkout_kwargs["customer_creation"] = "always"

    checkout = stripe.checkout.Session.create(**checkout_kwargs)
    return RedirectResponse(checkout.url, status_code=303)


# ----------------------------
# GET: Checkout success -> store IDs and activate PMC (then show success page)
# ----------------------------
@router.get("/pmc/onboarding/billing/success", response_class=HTMLResponse)
def onboarding_billing_success(
    request: Request,
    session_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """
    After Stripe checkout completes:
    - verify this session belongs to this PMC
    - store Stripe customer/subscription IDs
    - activate the PMC
    - show a friendly "Payment received" page (pmc_signup_success.html)
    """
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

    # Must be completed/paid
    cs_status = (cs.get("status") or "").strip().lower()
    payment_status = (cs.get("payment_status") or "").strip().lower()
    if cs_status != "complete" or payment_status not in {"paid", "no_payment_required"}:
        return RedirectResponse("/pmc/onboarding/billing-review", status_code=303)

    metadata = cs.get("metadata") or {}
    cs_type = (metadata.get("type") or "").strip()
    cs_pmc_id = (metadata.get("pmc_id") or "").strip()

    # Only accept the combined checkout here
    if cs_type != "pmc_full_activation":
        return RedirectResponse("/admin/dashboard#properties", status_code=303)

    # Prevent cross-account poisoning
    if cs_pmc_id and cs_pmc_id != str(pmc.id):
        raise HTTPException(status_code=403, detail="Checkout session does not match your account")

    checkout_session_id = cs.get("id")

    # Idempotency: if already processed, just show success page
    last_seen = getattr(pmc, "last_stripe_checkout_session_id", None)
    if not last_seen or last_seen != checkout_session_id:
        customer_id = cs.get("customer")          # cus_...
        subscription_id = cs.get("subscription")  # sub_...

        if customer_id:
            _set_if_attr(pmc, "stripe_customer_id", customer_id)
        if subscription_id:
            _set_if_attr(pmc, "stripe_subscription_id", subscription_id)

        _set_if_attr(pmc, "last_stripe_checkout_session_id", checkout_session_id)

        # Activate now (webhook can still backfill; this makes UX immediate)
        _set_if_attr(pmc, "billing_status", "active")
        _set_if_attr(pmc, "active", True)
        _set_if_attr(pmc, "sync_enabled", True)
        _set_if_attr(pmc, "signup_paid_at", datetime.now(timezone.utc))

        db.commit()

    # ✅ Show friendly success screen
    return templates.TemplateResponse(
        "pmc_signup_success.html",
        {
            "request": request,
            "session_id": session_id,
        },
    )
