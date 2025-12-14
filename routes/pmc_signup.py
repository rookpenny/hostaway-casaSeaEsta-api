# routes/pmc_signup.py
import os
import stripe
from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db
from models import PMC

router = APIRouter()
templates = Jinja2Templates(directory="templates")

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

PRICE_SIGNUP = os.getenv("STRIPE_PRICE_SIGNUP_ONETIME")
PRICE_PROPERTY = os.getenv("STRIPE_PRICE_PROPERTY_MONTHLY")
APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")

def _required_env():
    missing = []
    for k in ["STRIPE_SECRET_KEY", "STRIPE_PRICE_SIGNUP_ONETIME", "STRIPE_PRICE_PROPERTY_MONTHLY", "APP_BASE_URL"]:
        if not os.getenv(k):
            missing.append(k)
    if missing:
        raise HTTPException(status_code=500, detail=f"Missing env vars: {', '.join(missing)}")

@router.get("/pmc/signup", response_class=HTMLResponse)
def pmc_signup_page(request: Request):
    _required_env()
    return templates.TemplateResponse("pmc_signup.html", {"request": request})

@router.post("/pmc/signup")
def pmc_signup_start(
    request: Request,
    pmc_name: str = Form(...),
    admin_email: str = Form(...),
    admin_name: str = Form(""),
    db: Session = Depends(get_db),
):
    _required_env()
    email_l = (admin_email or "").strip().lower()
    if not email_l:
        raise HTTPException(status_code=400, detail="Email required")

    # Create PMC in pending state (NOT active until webhook confirms payment)
    pmc = PMC(
        pmc_name=(pmc_name or "").strip(),
        email=email_l,
        main_contact=(admin_name or "").strip() or None,
        active=False,                 # keep inactive until payment confirmed
        sync_enabled=False,
        billing_status="pending",
    )
    db.add(pmc)
    db.commit()
    db.refresh(pmc)

    success_url = f"{APP_BASE_URL}/pmc/signup/success"
    cancel_url = f"{APP_BASE_URL}/pmc/signup/cancel"

    # Checkout in subscription mode:
    # - recurring property price (quantity starts at 0)
    # - one-time signup fee added to first invoice via subscription_data.add_invoice_items
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer_email=email_l,
        line_items=[
            {"price": PRICE_PROPERTY, "quantity": 0},
        ],
        subscription_data={
            "metadata": {"pmc_id": str(pmc.id)},
            "add_invoice_items": [{"price": PRICE_SIGNUP}],
        },
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"pmc_id": str(pmc.id)},
    )

    return RedirectResponse(session.url, status_code=303)

@router.get("/pmc/signup/success", response_class=HTMLResponse)
def pmc_signup_success(request: Request):
    # IMPORTANT: This page is not proof of payment. Webhook is the truth. :contentReference[oaicite:4]{index=4}
    return HTMLResponse(
        "<h2>Thanks — processing your signup.</h2>"
        "<p>If your payment succeeded, your account will activate automatically.</p>"
        "<p>You can now log in at <a href='/auth/login'>/auth/login</a></p>"
    )

@router.get("/pmc/signup/cancel", response_class=HTMLResponse)
def pmc_signup_cancel(request: Request):
    return HTMLResponse("<h2>Signup canceled.</h2><p>You can try again anytime.</p>")
