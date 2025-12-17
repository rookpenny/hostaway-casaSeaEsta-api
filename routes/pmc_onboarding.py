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
from utils.billing import charge_property_for_month_if_needed


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
    api_secret: str = Form(...),
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
    db.commit()
    db.refresh(integ)

    try:
        synced = sync_properties(integration_id=integ.id)
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
        "synced=", synced,
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

    integ.last_synced_at = datetime.utcnow()
    db.commit()

    return RedirectResponse(f"/pmc/onboarding/properties?integration_id={integ.id}", status_code=303)


# ----------------------------
# GET: Choose properties to enable
# ----------------------------
@router.get("/pmc/onboarding/properties", response_class=HTMLResponse)
def onboarding_properties_page(
    request: Request,
    integration_id: int = Query(...),
    db: Session = Depends(get_db),
):
    redirect = _require_login_or_redirect(request, f"/pmc/onboarding/properties?integration_id={integration_id}")
    if redirect:
        return redirect

    pmc = _require_pmc_for_session(db, request)

    integ = (
        db.query(PMCIntegration)
        .filter(PMCIntegration.id == integration_id, PMCIntegration.pmc_id == pmc.id)
        .first()
    )
    if not integ:
        raise HTTPException(status_code=404, detail="Integration not found")

    properties = (
        db.query(Property)
        .filter(Property.integration_id == integ.id)
        .order_by(Property.property_name)
        .all()
    )

    return templates.TemplateResponse(
        "pmc_onboarding_properties.html",
        {"request": request, "pmc": pmc, "properties": properties, "integration_id": integ.id},
    )


# ----------------------------
# POST: Save property choices and go to billing review
# ----------------------------
@router.post("/pmc/onboarding/properties")
async def onboarding_properties_submit(
    request: Request,
    integration_id: int = Form(...),
    db: Session = Depends(get_db),
):
    redirect = _require_login_or_redirect(
        request, f"/pmc/onboarding/properties?integration_id={integration_id}"
    )
    if redirect:
        return redirect

    pmc = _require_pmc_for_session(db, request)

    integ = (
        db.query(PMCIntegration)
        .filter(PMCIntegration.id == integration_id, PMCIntegration.pmc_id == pmc.id)
        .first()
    )
    if not integ:
        raise HTTPException(status_code=404, detail="Integration not found")

    form = await request.form()

    selected_ids: set[int] = set()
    for pid in form.getlist("property_ids"):
        try:
            selected_ids.add(int(pid))
        except (ValueError, TypeError):
            continue

    props = (
        db.query(Property)
        .filter(Property.integration_id == integration_id)
        .all()
    )

    # Track transitions OFF -> ON so we can charge (only if already active)
    turned_on: list[Property] = []
    for prop in props:
        old = bool(prop.sandy_enabled)
        new = (prop.id in selected_ids)
        if (not old) and new:
            turned_on.append(prop)
        prop.sandy_enabled = new

    db.commit()

    # If already active, charge newly-enabled properties (once/month; ledger prevents doubles)
    if (getattr(pmc, "billing_status", None) or "").lower() == "active":
        try:
            for prop in turned_on:
                charge_property_for_month_if_needed(db, pmc, prop)
            db.commit()
        except Exception:
            db.rollback()
            # don’t block onboarding UI hard; they can retry toggling later if needed

    return RedirectResponse(
        f"/pmc/onboarding/billing-review?integration_id={integration_id}",
        status_code=303,
    )


# ----------------------------
# GET: Billing review screen
# ----------------------------
@router.get("/pmc/onboarding/billing-review", response_class=HTMLResponse)
def onboarding_billing_review(
    request: Request,
    integration_id: int = Query(...),
    db: Session = Depends(get_db),
):
    redirect = _require_login_or_redirect(
        request, f"/pmc/onboarding/billing-review?integration_id={integration_id}"
    )
    if redirect:
        return redirect

    try:
        pmc = _require_pmc_for_session(db, request)
    except HTTPException:
        return RedirectResponse("/pmc/signup", status_code=303)

    integ = (
        db.query(PMCIntegration)
        .filter(PMCIntegration.id == integration_id, PMCIntegration.pmc_id == pmc.id)
        .first()
    )
    if not integ:
        raise HTTPException(status_code=404, detail="Integration not found")

    enabled_count = (
        db.query(Property)
        .filter(
            Property.integration_id == integration_id,
            Property.sandy_enabled.is_(True),
        )
        .count()
    )

    # UI numbers (informational)
    setup_fee_cents = 49900
    monthly_cents_each = 999
    monthly_total_cents = enabled_count * monthly_cents_each
    due_today_cents = setup_fee_cents  # ✅ due today is setup only (monthly is charged on enable via invoicing)

    return templates.TemplateResponse(
        "pmc_onboarding_billing_review.html",
        {
            "request": request,
            "pmc": pmc,
            "integration_id": integration_id,
            "enabled_count": enabled_count,
            "setup_fee_cents": setup_fee_cents,
            "monthly_cents_each": monthly_cents_each,
            "monthly_total_cents": monthly_total_cents,
            "due_today_cents": due_today_cents,
        },
    )


# ----------------------------
# POST: Create Stripe checkout (SETUP FEE ONLY)
# ----------------------------
@router.post("/pmc/onboarding/billing/checkout")
def onboarding_billing_checkout(
    request: Request,
    integration_id: int = Form(...),
    db: Session = Depends(get_db),
):
    redirect = _require_login_or_redirect(
        request, f"/pmc/onboarding/billing-review?integration_id={integration_id}"
    )
    if redirect:
        return redirect

    pmc = _require_pmc_for_session(db, request)

    integ = (
        db.query(PMCIntegration)
        .filter(PMCIntegration.id == integration_id, PMCIntegration.pmc_id == pmc.id)
        .first()
    )
    if not integ:
        raise HTTPException(status_code=404, detail="Integration not found")

    enabled_count = (
        db.query(Property)
        .filter(Property.integration_id == integration_id, Property.sandy_enabled.is_(True))
        .count()
    )
    if enabled_count <= 0:
        return RedirectResponse(f"/pmc/onboarding/properties?integration_id={integration_id}", status_code=303)

    stripe_secret = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    app_base = (os.getenv("APP_BASE_URL") or "").rstrip("/")
    price_setup = (os.getenv("STRIPE_PRICE_SETUP_ONETIME") or "").strip()

    missing = []
    if not stripe_secret:
        missing.append("STRIPE_SECRET_KEY")
    if not app_base:
        missing.append("APP_BASE_URL")
    if not price_setup:
        missing.append("STRIPE_PRICE_SETUP_ONETIME")
    if missing:
        raise HTTPException(status_code=500, detail=f"Missing Stripe env vars: {', '.join(missing)}")

    stripe.api_key = stripe_secret

    email_l = _session_email(request)
    customer_id = (getattr(pmc, "stripe_customer_id", None) or "").strip() or None

    checkout_kwargs = dict(
        mode="payment",
        line_items=[{"price": price_setup, "quantity": 1}],
        success_url=f"{app_base}/pmc/onboarding/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{app_base}/pmc/onboarding/billing-review?integration_id={integration_id}",
        metadata={
            "pmc_id": str(pmc.id),
            "integration_id": str(integration_id),
            "type": "pmc_setup_fee",
            "enabled_count": str(enabled_count),
        },
    )

    if customer_id:
        checkout_kwargs["customer"] = customer_id
    else:
        # Payment-mode supports customer_creation
        checkout_kwargs["customer_email"] = email_l or pmc.email
        checkout_kwargs["customer_creation"] = "always"

    try:
        checkout = stripe.checkout.Session.create(**checkout_kwargs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stripe checkout failed: {str(e)}")

    return RedirectResponse(checkout.url, status_code=303)


# ----------------------------
# GET: Checkout success -> store customer + activate PMC + charge enabled properties for month
# ----------------------------
@router.get("/pmc/onboarding/billing/success", response_class=HTMLResponse)
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

    cs_status = (cs.get("status") or "").strip().lower()
    payment_status = (cs.get("payment_status") or "").strip().lower()
    if cs_status != "complete" or payment_status not in {"paid", "no_payment_required"}:
        return RedirectResponse("/pmc/onboarding/billing-review", status_code=303)

    metadata = cs.get("metadata") or {}
    cs_type = (metadata.get("type") or "").strip()
    cs_pmc_id = (metadata.get("pmc_id") or "").strip()

    if cs_type != "pmc_setup_fee":
        return RedirectResponse("/admin/dashboard#properties", status_code=303)

    if cs_pmc_id and cs_pmc_id != str(pmc.id):
        raise HTTPException(status_code=403, detail="Checkout session does not match your account")

    checkout_session_id = cs.get("id")
    last_seen = getattr(pmc, "last_stripe_checkout_session_id", None)

    if not last_seen or last_seen != checkout_session_id:
        customer_id = cs.get("customer")  # cus_...

        if customer_id:
            _set_if_attr(pmc, "stripe_customer_id", customer_id)

        _set_if_attr(pmc, "last_stripe_checkout_session_id", checkout_session_id)
        _set_if_attr(pmc, "billing_status", "active")
        _set_if_attr(pmc, "active", True)
        _set_if_attr(pmc, "sync_enabled", True)
        _set_if_attr(pmc, "signup_paid_at", datetime.now(timezone.utc))

        db.commit()

        # After activation, charge any currently-enabled properties for this month (idempotent)
        try:
            integration_id = int((metadata.get("integration_id") or "0") or 0)
        except Exception:
            integration_id = 0

        if integration_id:
            enabled_props = (
                db.query(Property)
                .filter(Property.integration_id == integration_id, Property.sandy_enabled.is_(True))
                .all()
            )
            try:
                for prop in enabled_props:
                    charge_property_for_month_if_needed(db, pmc, prop)
                db.commit()
            except Exception:
                db.rollback()

    return templates.TemplateResponse(
        "pmc_signup_success.html",
        {
            "request": request,
            "session_id": session_id,
        },
    )
