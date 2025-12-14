# routes/stripe_webhook.py
import os
import stripe

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from database import SessionLocal
from models import PMC

router = APIRouter()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

def _require_env():
    if not stripe.api_key or not WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Stripe webhook env not configured")

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

    # ✅ Signup payment completion (your pmc_signup uses mode="payment")
    if event_type == "checkout.session.completed":
        metadata = obj.get("metadata") or {}
        pmc_id = metadata.get("pmc_id")

        # Only handle our signup checkout
        if metadata.get("type") != "pmc_signup_onetime":
            return JSONResponse({"ok": True})

        if pmc_id:
            db: Session = SessionLocal()
            try:
                pmc = db.query(PMC).filter(PMC.id == int(pmc_id)).first()
                if pmc:
                    pmc.active = True
                    pmc.sync_enabled = True
                    if hasattr(pmc, "billing_status"):
                        pmc.billing_status = "active"
                    if hasattr(pmc, "stripe_customer_id"):
                        pmc.stripe_customer_id = obj.get("customer")
                    db.commit()
            finally:
                db.close()

    return JSONResponse({"ok": True})
