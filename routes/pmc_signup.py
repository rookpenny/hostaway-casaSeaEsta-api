# routes/pmc_signup.py
import os
import stripe

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from models import PMC, PMCUser

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

PRICE_SIGNUP_ONETIME = os.getenv("STRIPE_PRICE_SIGNUP_ONETIME", "")
PRICE_PROPERTY_MONTHLY = os.getenv("STRIPE_PRICE_PROPERTY_MONTHLY", "")  # used later
APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")


def _required_env() -> None:
    missing = []
    for k in [
        "STRIPE_SECRET_KEY",
        "STRIPE_PRICE_SIGNUP_ONETIME",
        "STRIPE_PRICE_PROPERTY_MONTHLY",
        "APP_BASE_URL",
    ]:
        if not os.getenv(k):
            missing.append(k)
    if missing:
        raise HTTPException(status_code=500, detail=f"Missing env vars: {', '.join(missing)}")


def _session_email(request: Request) -> str | None:
    user = request.session.get("user") or {}
    email = (user.get("email") or "").strip().lower()
    return email or None


def _session_name(request: Request) -> str | None:
    user = request.session.get("user") or {}
    name = (user.get("name") or "").strip()
    return name or None


def _set_if_attr(obj, attr: str, value) -> None:
    if hasattr(obj, attr):
        setattr(obj, attr, value)


@router.get("/pmc/signup", response_class=HTMLResponse)
def pmc_signup_page(request: Request):
    _required_env()
    return templates.TemplateResponse(
        "pmc_signup.html",
        {
            "request": request,
            "pmc_name_prefill": "",
            "admin_name_prefill": _session_name(request) or "",
            "admin_email_prefill": _session_email(request) or "",
        },
    )


@router.post("/pmc/signup")
def pmc_signup_start(
    request: Request,
    pmc_name: str = Form(...),
    admin_name: str = Form(""),
    admin_email: str = Form(""),  # accepted for UX, but session email is preferred
    db: Session = Depends(get_db),
):
    """
    Create/reuse PMC in locked state, start Stripe Checkout for one-time signup fee.
    Activation + billing fields are set by Stripe webhook after payment succeeds.
    """
    _required_env()

    pmc_name_clean = (pmc_name or "").strip()
    if not pmc_name_clean:
        raise HTTPException(status_code=400, detail="PMC name is required")

    # Prefer Google session email (trust boundary); fallback to form field if needed
    email_l = _session_email(request) or (admin_email or "").strip().lower()
    if not email_l:
        raise HTTPException(
            status_code=400,
            detail="Admin email is required (recommended: sign in with Google first).",
        )

    admin_name_clean = (_session_name(request) or admin_name or "").strip() or None

    # --- Find or create PMC ---
    pmc = (
        db.query(PMC)
        .filter(func.lower(PMC.email) == email_l)
        .order_by(PMC.id.desc())
        .first()
    )

    if pmc:
        # If already active, skip charging again
        billing_status = (getattr(pmc, "billing_status", "") or "").lower()
        if bool(getattr(pmc, "active", False)) and billing_status == "active":
            return RedirectResponse("/admin/dashboard", status_code=303)

        # Reuse existing record but ensure it's locked
        pmc.pmc_name = pmc_name_clean
        pmc.main_contact = admin_name_clean
        pmc.active = False
        pmc.sync_enabled = False
        _set_if_attr(pmc, "billing_status", "pending")
        db.commit()
        db.refresh(pmc)
    else:
        pmc = PMC(
            pmc_name=pmc_name_clean,
            email=email_l,
            main_contact=admin_name_clean,
            active=False,
            sync_enabled=False,
        )
        _set_if_attr(pmc, "billing_status", "pending")
        db.add(pmc)
        db.commit()
        db.refresh(pmc)

    # --- Ensure PMCUser exists (owner) ---
    existing_user = (
        db.query(PMCUser)
        .filter(
            PMCUser.pmc_id == pmc.id,
            func.lower(PMCUser.email) == email_l,
        )
        .first()
    )
    if not existing_user:
        pmc_user = PMCUser(
            pmc_id=pmc.id,
            email=email_l,
            full_name=admin_name_clean,
            role="owner",
            is_active=True,
        )
        db.add(pmc_user)
        db.commit()

    # --- Stripe Checkout (one-time signup fee) ---
    success_url = f"{APP_BASE_URL}/pmc/signup/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{APP_BASE_URL}/pmc/signup/cancel"

    try:
        # Configure a one‑time payment for the signup fee and save the card on file
        checkout = stripe.checkout.Session.create(
            mode="payment",
            customer_email=email_l,
            
            # Force Stripe to create a Customer so obj["customer"] isn’t None in the webhook
            customer_creation="always",
            
            # Save the payment method for future monthly subscriptions
            payment_intent_data={"setup_future_usage": "off_session"},
            line_items=[
                {"price": PRICE_SIGNUP_ONETIME, "quantity": 1},
            ],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "pmc_id": str(pmc.id),
                "type": "pmc_signup_onetime",
            },
        )


    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stripe checkout failed: {str(e)}")

    return RedirectResponse(checkout.url, status_code=303)



@router.get("/pmc/signup/success", response_class=HTMLResponse)
def pmc_signup_success(request: Request, db: Session = Depends(get_db), session_id: str | None = None):
    """
    This page is NOT proof of payment. Webhook is truth.
    But we can route them into onboarding.
    """
    user = request.session.get("user") or {}
    email_l = (user.get("email") or "").strip().lower()

    # If not logged in, send them to login, then return here
    if not email_l:
        request.session["post_login_redirect"] = "/pmc/signup/success"
        return RedirectResponse("/auth/login/google?next=/pmc/signup/success", status_code=303)

    pmc = (
        db.query(PMC)
        .filter(func.lower(PMC.email) == email_l)
        .order_by(PMC.id.desc())
        .first()
    )

    # If webhook hasn't updated yet, show your success template (or a simple message)
    if not pmc or (getattr(pmc, "billing_status", "") or "").lower() != "active":
        return templates.TemplateResponse(
            "pmc_signup_success.html",
            {
                "request": request,
                "session_id": session_id or "",
                "next_url": "/pmc/onboarding/pms",
                "is_paid": False,
            },
        )

    # Paid: go to onboarding PMS connect
    return RedirectResponse("/pmc/onboarding/pms", status_code=303)



@router.get("/pmc/signup/cancel", response_class=HTMLResponse)
def pmc_signup_cancel(request: Request):
    return templates.TemplateResponse("pmc_signup_cancel.html", {"request": request})
