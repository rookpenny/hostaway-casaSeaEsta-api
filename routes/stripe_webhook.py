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

    # We only care about successful checkout completion
    if event_type != "checkout.session.completed":
        return JSONResponse({"ok": True})

    metadata = obj.get("metadata") or {}
    pmc_id = metadata.get("pmc_id")

    # Stripe fields we can use to find the PMC even if metadata is missing
    customer_id = obj.get("customer")  # cus_...
    checkout_session_id = obj.get("id")  # cs_test_...
    email = None
    try:
        email = (obj.get("customer_details") or {}).get("email") or obj.get("customer_email")
    except Exception:
        email = None
    email_l = (email or "").strip().lower()

    db: Session = SessionLocal()
    try:
        pmc = None

        # 1) Preferred: metadata pmc_id
        if pmc_id:
            pmc = db.query(PMC).filter(PMC.id == int(pmc_id)).first()

        # 2) Fallback: match PMC.email (latest pending/inactive first)
        if pmc is None and email_l:
            pmc = (
                db.query(PMC)
                .filter(PMC.email == email_l)
                .order_by(PMC.id.desc())
                .first()
            )

        if pmc is None:
            # IMPORTANT: return 200 so Stripe stops retrying,
            # but log enough to diagnose.
            print("[stripe_webhook] No PMC matched. pmc_id=", pmc_id, "email=", email_l, "session=", checkout_session_id)
            return JSONResponse({"ok": True})

        # ✅ Activate / mark paid
        pmc.active = True
        pmc.sync_enabled = True

        # Persist billing info if the columns exist
        _set_if_attr(pmc, "billing_status", "active")
        _set_if_attr(pmc, "stripe_customer_id", customer_id)
        _set_if_attr(pmc, "signup_paid_at", datetime.now(timezone.utc))
        _set_if_attr(pmc, "last_stripe_checkout_session_id", checkout_session_id)

        db.commit()
        print("[stripe_webhook] Activated PMC", pmc.id, "email=", pmc.email, "customer=", customer_id)

    finally:
        db.close()

    return JSONResponse({"ok": True})
