# routes/stripe_webhook.py
import os
import stripe
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from database import SessionLocal
from models import PMC

router = APIRouter()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

def _require_env():
    missing = []
    if not stripe.api_key:
        missing.append("STRIPE_SECRET_KEY")
    if not WEBHOOK_SECRET:
        missing.append("STRIPE_WEBHOOK_SECRET")
    if missing:
        raise HTTPException(status_code=500, detail=f"Missing env vars: {', '.join(missing)}")

def _set_if_attr(obj, attr: str, value):
    """Set attribute only if the ORM model has that column/attr."""
    if hasattr(obj, attr):
        setattr(obj, attr, value)

@router.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    _require_env()

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing Stripe signature header")

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=WEBHOOK_SECRET,
        )
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook parse error: {str(e)}")

    event_type = event.get("type", "")
    obj = (event.get("data", {}) or {}).get("object", {}) or {}

    # We only act on our signup checkout completion
    if event_type == "checkout.session.completed":
        metadata = obj.get("metadata") or {}
        if metadata.get("type") != "pmc_signup_onetime":
            return JSONResponse({"ok": True})

        pmc_id = metadata.get("pmc_id")
        if not pmc_id:
            return JSONResponse({"ok": True})

        customer_id = obj.get("customer")  # cus_...
        checkout_session_id = obj.get("id")  # cs_test_...

        db: Session = SessionLocal()
        try:
            pmc = db.query(PMC).filter(PMC.id == int(pmc_id)).first()
            if not pmc:
                return JSONResponse({"ok": True})

            # ✅ Activate the account
            pmc.active = True
            pmc.sync_enabled = True

            # ✅ Persist billing metadata (if columns exist)
            _set_if_attr(pmc, "billing_status", "active")
            _set_if_attr(pmc, "stripe_customer_id", customer_id)
            _set_if_attr(pmc, "signup_paid_at", datetime.now(timezone.utc))
            _set_if_attr(pmc, "last_stripe_checkout_session_id", checkout_session_id)

            db.commit()
        finally:
            db.close()

    return JSONResponse({"ok": True})
