# routes/stripe_webhook.py
import os
import stripe
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import SessionLocal
from models import PMC

router = APIRouter()


# ----------------------------
# Helpers
# ----------------------------
def _load_env() -> tuple[str, str]:
    stripe_secret = os.getenv("STRIPE_SECRET_KEY", "").strip()
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
    return stripe_secret, webhook_secret


def _require_env() -> tuple[str, str]:
    stripe_secret, webhook_secret = _load_env()
    missing = []
    if not stripe_secret:
        missing.append("STRIPE_SECRET_KEY")
    if not webhook_secret:
        missing.append("STRIPE_WEBHOOK_SECRET")
    if missing:
        raise HTTPException(status_code=500, detail=f"Missing env vars: {', '.join(missing)}")
    return stripe_secret, webhook_secret


def _set_if_attr(obj, attr: str, value) -> None:
    # Only set if your DB/model actually has the column
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


# ----------------------------
# Webhook
# ----------------------------
@router.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    stripe_secret, webhook_secret = _require_env()
    stripe.api_key = stripe_secret

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing Stripe signature header")

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=webhook_secret,
        )
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook parse error: {str(e)}")

    event_type = event.get("type", "")
    obj = ((event.get("data") or {}).get("object")) or {}

    # Only handle successful checkout completion
    if event_type != "checkout.session.completed":
        return JSONResponse({"ok": True})

    metadata = obj.get("metadata") or {}
    pmc_id = metadata.get("pmc_id")
    checkout_type = (metadata.get("type") or "").strip()

    # Allowlist the checkout types we care about
    ALLOWED_TYPES = {"pmc_signup_onetime", "pmc_property_subscription"}
    if checkout_type and checkout_type not in ALLOWED_TYPES:
        return JSONResponse({"ok": True})

    customer_id = obj.get("customer")         # cus_... (requires customer_creation="always" on signup checkout)
    checkout_session_id = obj.get("id")       # cs_...
    subscription_id = obj.get("subscription") # sub_... (mode="subscription")
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

        # 2) Fallback: match PMC.email (latest wins) - normalize case
        if pmc is None and email_l:
            pmc = (
                db.query(PMC)
                .filter(func.lower(PMC.email) == email_l)
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
                "subscription=", subscription_id,
                "type=", checkout_type,
            )
            return JSONResponse({"ok": True})

        # Optional idempotency: if we've already recorded this session id, do nothing
        last_seen = getattr(pmc, "last_stripe_checkout_session_id", None)
        if last_seen and last_seen == checkout_session_id:
            return JSONResponse({"ok": True})

        # Always ensure PMC is active once we receive a successful checkout event
        pmc.active = True
        pmc.sync_enabled = True

        # Save customer id when present
        if customer_id:
            _set_if_attr(pmc, "stripe_customer_id", customer_id)

        # Record the latest checkout session id (generic)
        _set_if_attr(pmc, "last_stripe_checkout_session_id", checkout_session_id)

        # Branch by checkout type
        if checkout_type == "pmc_signup_onetime":
            # Signup fee: mark billing as active + store paid timestamp
            _set_if_attr(pmc, "billing_status", "active")
            _set_if_attr(pmc, "signup_paid_at", datetime.now(timezone.utc))
            # Store the signup session id (matches your model field)
            _set_if_attr(pmc, "stripe_signup_checkout_session_id", checkout_session_id)

        elif checkout_type == "pmc_property_subscription":
            # Property subscription: record subscription id
            if subscription_id:
                _set_if_attr(pmc, "stripe_subscription_id", subscription_id)
            # Do NOT overwrite billing_status here unless you want subscriptions to activate billing too.
            # If you DO want it, uncomment:
            # _set_if_attr(pmc, "billing_status", "active")

        db.commit()

        print(
            "[stripe_webhook] Processed checkout",
            "pmc_id=", pmc.id,
            "email=", pmc.email,
            "type=", checkout_type,
            "customer=", customer_id,
            "session=", checkout_session_id,
            "subscription=", subscription_id,
        )

    finally:
        db.close()

    return JSONResponse({"ok": True})
