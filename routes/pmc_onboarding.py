import os
import stripe
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from models import PMC, PMCIntegration, PMCUser, Property




# IMPORTANT: adjust this import to your real path if needed:
# - project root: from pms_sync import sync_properties
# - utils folder: from utils.pms_sync import sync_properties
from utils.pms_sync import sync_properties

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# List of PMS providers that can be selected during onboarding.
# When adding support for a new provider, add its slug here and create a corresponding import route.
SUPPORTED_PROVIDERS = {"hostaway", "lodgify", "guesty"}


# ----------------------------
# Session helpers
# ----------------------------
def _session_user(request: Request) -> dict:
    return request.session.get("user") or {}


def _session_email(request: Request) -> Optional[str]:
    email = (_session_user(request).get("email") or "").strip().lower()
    return email or None


def _redirect_to_google_login(next_path: str) -> RedirectResponse:
    # keep query param simple and consistent
    return RedirectResponse(url=f"/auth/login/google?next={next_path}", status_code=302)


def _require_login_or_redirect(request: Request, next_path: str) -> Optional[RedirectResponse]:
    if not _session_email(request):
        return _redirect_to_google_login(next_path)
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
        # Guard: don’t allow onboarding if they didn’t pay
        return RedirectResponse("/pmc/signup", status_code=303)

    provider = "hostaway"  # default for now; later you can accept ?provider=
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

    provider = "hostaway"
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=400, detail="Unsupported provider")

    account_id = (account_id or "").strip()
    api_key = (api_key or "").strip()
    api_secret = (api_secret or "").strip()
    if not account_id or not api_key or not api_secret:
        raise HTTPException(status_code=400, detail="Missing Hostaway credentials")

    # 1) Upsert provider-agnostic integration record
    integ = (
        db.query(PMCIntegration)
        .filter(PMCIntegration.pmc_id == pmc.id, PMCIntegration.provider == provider)
        .first()
    )
    if not integ:
        integ = PMCIntegration(pmc_id=pmc.id, provider=provider)
        db.add(integ)

    integ.account_id = account_id
    integ.api_key = api_key
    integ.api_secret = api_secret
    integ.is_connected = True
    integ.updated_at = datetime.utcnow()

    # 2) Mirror into PMC table (backwards compatible with existing code paths)
    # Only set if those columns exist on PMC (safe across migrations)
    if hasattr(pmc, "pms_integration"):
        pmc.pms_integration = provider
    if hasattr(pmc, "pms_account_id"):
        pmc.pms_account_id = account_id
    if hasattr(pmc, "pms_api_key"):
        pmc.pms_api_key = api_key
    if hasattr(pmc, "pms_api_secret"):
        pmc.pms_api_secret = api_secret

    db.commit()
    db.refresh(integ)
    db.refresh(pmc)

    # 3) Import properties using your existing sync util
    # NOTE: your current sync_properties(account_id) looks up creds by account_id
    # via fetch_pmc_lookup(). That must include this PMC + creds.
    try:
        count = sync_properties(account_id=str(account_id))
    except Exception as e:
        # Keep creds saved so they can fix + retry
        existing = integ
        return _render_pms_page(
            request,
            pmc,
            existing,
            error=f"Hostaway import failed: {str(e)}",
            provider=provider,
        )

    # 4) Mark last sync (nice for UI)
    if hasattr(integ, "last_synced_at"):
        integ.last_synced_at = datetime.utcnow()
        db.commit()

    # Land them directly on properties view (optional)
    #return RedirectResponse("/admin/dashboard#properties", status_code=303)
    return RedirectResponse("/pmc/onboarding/properties", status_code=303)

# ----------------------------
# POST: Save creds + import properties (Logify v1)
# ----------------------------

@router.post("/pmc/onboarding/pms/lodgify/import")
def onboarding_lodgify_import(request: Request, db: Session = Depends(get_db)):
    """
    Placeholder endpoint for Lodgify PMS integrations.
    Once Lodgify support is implemented, this endpoint should accept the
    necessary credentials (e.g., API key, secret) and import properties.
    """
    redirect = _require_login_or_redirect(request, "/pmc/onboarding/pms")
    if redirect:
        return redirect
    pmc = _require_pmc_for_session(db, request)
    if not _is_paid(pmc):
        return RedirectResponse("/pmc/signup", status_code=303)
    raise HTTPException(status_code=501, detail="Lodgify integration is coming soon")

# ----------------------------
# POST: Save creds + import properties (Guesty v1)
# ----------------------------
@router.post("/pmc/onboarding/pms/guesty/import")
def onboarding_guesty_import(request: Request, db: Session = Depends(get_db)):
    """
    Placeholder endpoint for Guesty PMS integrations.
    Once Guesty support is implemented, this endpoint should accept the
    necessary credentials (e.g., API token) and import properties.
    """
    redirect = _require_login_or_redirect(request, "/pmc/onboarding/pms")
    if redirect:
        return redirect
    pmc = _require_pmc_for_session(db, request)
    if not _is_paid(pmc):
        return RedirectResponse("/pmc/signup", status_code=303)
    raise HTTPException(status_code=501, detail="Guesty integration is coming soon")

# ----------------------------
# GET/POST: Choose properties after import
# ----------------------------

@router.get("/pmc/onboarding/properties", response_class=HTMLResponse)
def onboarding_properties_page(request: Request, db: Session = Depends(get_db)):
    """
    Display a list of imported properties for the PMC and allow the user to
    choose which ones to enable Sandy for. This step follows a successful
    PMS import and precedes unlocking the admin dashboard.
    """
    redirect = _require_login_or_redirect(request, "/pmc/onboarding/properties")
    if redirect:
        return redirect
    pmc = _require_pmc_for_session(db, request)
    if not _is_paid(pmc):
        return RedirectResponse("/pmc/signup", status_code=303)
    # Fetch all properties belonging to this PMC, sorted by name
    properties = (
        db.query(Property)
        .filter(Property.pmc_id == pmc.id)
        .order_by(Property.property_name)
        .all()
    )
    return templates.TemplateResponse(
        "pmc_onboarding_properties.html",
        {
            "request": request,
            "pmc": pmc,
            "properties": properties,
        },
    )



@router.post("/pmc/onboarding/properties")
async def onboarding_properties_submit(request: Request, db: Session = Depends(get_db)):
    """
    Process the property selection form. The submitted form contains a list of
    property IDs that the user wants to enable. All other properties will be
    left disabled. After updating the database, either redirect to the admin
    dashboard or send the user through a subscription checkout if one or more
    properties were selected.
    """
    redirect = _require_login_or_redirect(request, "/pmc/onboarding/properties")
    if redirect:
        return redirect

    pmc = _require_pmc_for_session(db, request)
    if not _is_paid(pmc):
        return RedirectResponse("/pmc/signup", status_code=303)

    form = await request.form()
    # Extract selected property IDs from the form (checkboxes share the same name)
    selected_ids = set()
    for pid in form.getlist("property_ids"):
        try:
            selected_ids.add(int(pid))
        except (ValueError, TypeError):
            continue

    # Update each property: enable if selected, disable otherwise
    props = db.query(Property).filter(Property.pmc_id == pmc.id).all()
    for prop in props:
        prop.sandy_enabled = prop.id in selected_ids
    db.commit()

    # Determine how many properties were enabled
    enabled_count = len(selected_ids)
    # If none selected, nothing to bill; send user straight to the dashboard
    if enabled_count <= 0:
        return RedirectResponse("/admin/dashboard", status_code=303)

    # Ensure we have a Stripe Customer ID from the signup checkout
    customer_id = getattr(pmc, "stripe_customer_id", None)
    if not customer_id:
        # Without a customer ID we can't start a subscription; require signup
        return RedirectResponse("/pmc/signup", status_code=303)

    # Configure Stripe
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    PRICE_PROPERTY_MONTHLY = os.getenv("STRIPE_PRICE_PROPERTY_MONTHLY", "").strip()
    APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")

    # Create a subscription checkout session with quantity = enabled_count
    checkout = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[
            {"price": PRICE_PROPERTY_MONTHLY, "quantity": enabled_count},
        ],
        success_url=f"{APP_BASE_URL}/pmc/onboarding/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{APP_BASE_URL}/pmc/onboarding/properties",
        metadata={
            "pmc_id": str(pmc.id),
            "type": "pmc_property_subscription",
            "quantity": str(enabled_count),
        },
    )
    # Redirect the user to Stripe to confirm the subscription
    return RedirectResponse(checkout.url, status_code=303)


@router.get("/pmc/onboarding/billing/success")
def onboarding_billing_success(
    request: Request,
    session_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """
    Stripe subscription checkout success handler.
    This makes onboarding deterministic by immediately storing:
      - pmc.stripe_subscription_id
      - pmc.stripe_customer_id (if missing)
    Then redirects user to dashboard.
    """
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

    # Basic integrity checks
    metadata = cs.get("metadata") or {}
    cs_pmc_id = (metadata.get("pmc_id") or "").strip()
    cs_type = (metadata.get("type") or "").strip()

    if cs_type and cs_type != "pmc_property_subscription":
        # If someone hits this endpoint from the wrong checkout type, just send them onward
        return RedirectResponse("/admin/dashboard#properties", status_code=303)

    # Ensure the checkout session matches this PMC (prevents cross-account poisoning)
    if cs_pmc_id and str(pmc.id) != cs_pmc_id:
        raise HTTPException(status_code=403, detail="Checkout session does not match your account")

    subscription_id = cs.get("subscription")  # sub_...
    customer_id = cs.get("customer")          # cus_...

    if subscription_id:
        if hasattr(pmc, "stripe_subscription_id"):
            pmc.stripe_subscription_id = subscription_id

    if customer_id and not getattr(pmc, "stripe_customer_id", None):
        if hasattr(pmc, "stripe_customer_id"):
            pmc.stripe_customer_id = customer_id

    # Record latest checkout session for traceability
    _set_if_attr(pmc, "last_stripe_checkout_session_id", cs.get("id"))

    db.commit()

    return RedirectResponse("/admin/dashboard#properties", status_code=303)

