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
PRICE_PROPERTY_MONTHLY = os.getenv("STRIPE_PRICE_PROPERTY_MONTHLY", "")  # used later when properties are added
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


@router.get("/pmc/signup", response_class=HTMLResponse)
def pmc_signup_page(request: Request):
    _required_env()

    # Prefill from Google session if available (recommended)
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
    # NOTE: we still accept the field for UX, but we do NOT trust it.
    admin_email: str = Form(""),
    db: Session = Depends(get_db),
):
    """
    Creates a PMC in a locked state, then starts Stripe Checkout for ONE-TIME signup fee.
    Account activation is done ONLY by webhook after payment succeeds.
    """
    _required_env()

    pmc_name_clean = (pmc_name or "").strip()
    if not pmc_name_clean:
        raise HTTPException(status_code=400, detail="PMC name is required")

    # ✅ DO NOT trust form email. Use Google session email if present.
    email_l = _session_email(request) or (admin_email or "").strip().lower()
    if not email_l:
        raise HTTPException(
            status_code=400,
            detail="Admin email is required (recommended: sign in with Google first).",
        )

    admin_name_clean = (_session_name(request) or admin_name or "").strip() or None

    # Prevent duplicates: reuse existing PMC for this email if it already exists
    existing = (
        db.query(PMC)
        .filter(func.lower(PMC.email) == email_l)
        .order_by(PMC.id.desc())
        .first()
    )

    if existing:
        # If already active, no need to pay signup again
        if bool(getattr(existing, "active", False)) and (getattr(existing, "billing_status", "") or "").lower() == "active":
            return RedirectResponse("/admin/dashboard", status_code=303)

        # If pending/inactive, reuse it (and ensure it’s locked)
        pmc = existing
        pmc.pmc_name = pmc_name_clean
        pmc.main_contact = admin_name_clean
        pmc.active = False
        pmc.sync_enabled = False
        if hasattr(pmc, "billing_status"):
            pmc.billing_status = (getattr(pmc, "billing_status", None) or "pending")
        db.commit()
        db.refresh(pmc)
    else:
        # Create PMC in locked state
        pmc = PMC(
            pmc_name=pmc_name_clean,
            email=email_l,
            main_contact=admin_name_clean,
            active=False,        # 🔒 locked until webhook confirms payment
            sync_enabled=False,  # 🔒 locked until paid
        )
        # If your PMC model has billing_status, set it; if not, this is a no-op.
        if hasattr(pmc, "billing_status"):
            pmc.billing_status = "pending"

        db.add(pmc)
        db.commit()
        db.refresh(pmc)

        # Create the PMC admin user record too (so OAuth membership is explicit)
        # role: owner | admin | staff (you already commented this convention in the model)
        pmc_user = PMCUser(
            pmc_id=pmc.id,
            email=email_l,
            full_name=admin_name_clean,
            role="owner",
            is_active=True,
        )
        db.add(pmc_user)
        db.commit()

    # Stripe Checkout (ONE-TIME signup fee)
    success_url = f"{APP_BASE_URL}/pmc/signup/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{APP_BASE_URL}/pmc/signup/cancel"

    try:
        checkout = stripe.checkout.Session.create(
            mode="payment",
            customer_email=email_l,
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
def pmc_signup_success(request: Request, session_id: str | None = None):
    # ✅ IMPORTANT: This page is NOT proof of payment. Webhook is the truth.
    # Keep it simple: tell them to log in / refresh.
    return templates.TemplateResponse(
        "pmc_signup_success.html",
        {
            "request": request,
            "session_id": session_id or "",
        },
    )


@router.get("/pmc/signup/cancel", response_class=HTMLResponse)
def pmc_signup_cancel(request: Request):
    return templates.TemplateResponse("pmc_signup_cancel.html", {"request": request})
