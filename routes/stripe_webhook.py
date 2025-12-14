# routes/stripe_webhook.py
import os
import stripe
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from database import SessionLocal
from models import PMC

router = APIRouter()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()


def _require_env() -> None:
    missing = []
    if not stripe.api_key:
        missing.append("STRIPE_SECRET_KEY")
    if not WEBHOOK_SECRET:
        missing.append("STRIPE_WEBHOOK_SECRET")
    if missing:
        raise HTTPException(status_code=500, detail=f"Missing env vars: {', '.join(missing)}")


def _set_if_attr(obj, attr: str, value) -> None:
    # only set if your DB/model actually has the column
    if hasattr(obj, attr):
        setattr(obj, attr, value)


def _get_email_from_session(obj: dict) -> Optional[str]:
    # Stripe can put email in a few places depending on Checkout config
    try:
        cd = obj.get("customer_details") or {}
        email = cd.get("email") or obj.get("customer_email")
        return (email or "").strip().lower() or None
    except Exception:
        return None


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
    obj = ((event.get("data") or {}).get("object")) or {}

    # ✅ Only handle successful checkout completion
    if event_type != "checkout.session.completed":
        return JSONResponse({"ok": True})

    metadata = obj.get("metadata") or {}
    pmc_id = metadata.get("pmc_id")
    checkout_type = (metadata.get("type") or "").strip()  # e.g. "pmc_signup_onetime"

    # If you want to ONLY activate on the signup checkout, keep this guard:
    if checkout_type and checkout_type != "pmc_signup_onetime":
        return JSONResponse({"ok": True})

    customer_id = obj.get("customer")  # cus_... (will be None unless you set customer_creation="always")
    checkout_session_id = obj.get("id")  # cs_...
    email_l = _get_email_from_session(obj)

    db: Session = SessionLocal()
    try:
        pmc: Optional[PMC] = None

        # 1) Preferred: metadata pmc_id
        if pmc_id:
            try:
                pmc = db.query(PMC).filter(PMC.id == int(pmc_id)).first()
            except Exception:
                pmc = None

        # 2) Fallback: match PMC.email (latest wins)
        if pmc is None and email_l:
            pmc = (
                db.query(PMC)
                .filter(PMC.email == email_l)
                .order_by(PMC.id.desc())
                .first()
            )

        if pmc is None:
            # Return 200 so Stripe doesn't retry forever, but log what we saw.
            print(
                "[stripe_webhook] No PMC matched.",
                "pmc_id=", pmc_id,
                "email=", email_l,
                "session=", checkout_session_id,
                "customer=", customer_id,
            )
            return JSONResponse({"ok": True})

        # ✅ Mark active/paid
        pmc.active = True
        pmc.sync_enabled = True

        # Persist billing data (only if columns exist)
        _set_if_attr(pmc, "billing_status", "active")
        _set_if_attr(pmc, "signup_paid_at", datetime.now(timezone.utc))

        # customer may be None unless you force customer creation in Checkout
        if customer_id:
            _set_if_attr(pmc, "stripe_customer_id", customer_id)

        _set_if_attr(pmc, "last_stripe_checkout_session_id", checkout_session_id)

        # If you later move to subscriptions, this will become available:
        subscription_id = obj.get("subscription")  # sub_... (mode="subscription")
        if subscription_id:
            _set_if_attr(pmc, "stripe_subscription_id", subscription_id)

        db.commit()

        print(
            "[stripe_webhook] Activated PMC",
            pmc.id,
            "email=", pmc.email,
            "customer=", customer_id,
            "session=", checkout_session_id,
            "subscription=", subscription_id,
        )

    finally:
        db.close()

    return JSONResponse({"ok": True})
