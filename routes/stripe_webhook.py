# Keep your existing /stripe/webhook endpoint (for PMC billing)
# Add Stripe Connect onboarding endpoints (for PMCs to connect their own Stripe)
# Add guest upgrade Checkout Session (runs on the PMC’s connected Stripe account)
# Extend your existing webhook to also mark upgrade purchases as paid (without breaking PMC signup logic)


# routes/stripe_webhook.py
import os
from datetime import datetime, timezone
from typing import Optional

import stripe
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import SessionLocal
from models import PMC  # keep your existing PMC model import

router = APIRouter()


# ----------------------------
# Helpers
# ----------------------------
def _load_env() -> tuple[str, str]:
    stripe_secret = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    webhook_secret = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
    return stripe_secret, webhook_secret


def _require_env() -> tuple[str, str]:
    stripe_secret, webhook_secret = _load_env()

    missing = []
    if not stripe_secret:
        missing.append("STRIPE_SECRET_KEY")
    if not webhook_secret:
        missing.append("STRIPE_WEBHOOK_SECRET")

    if missing:
        raise HTTPException(
            status_code=500,
            detail=f"Missing env vars: {', '.join(missing)}",
        )

    return stripe_secret, webhook_secret


def _set_if_attr(obj, attr: str, value) -> None:
    """
    Set an attribute only if it exists on the object.
    Useful for backward-compatible migrations.
    """
    if hasattr(obj, attr):
        setattr(obj, attr, value)


def _get_email_from_session(obj: dict) -> Optional[str]:
    """
    Extract a normalized email address from a Stripe Checkout session object.
    Works for both subscription and payment mode checkouts.
    """
    if not isinstance(obj, dict):
        return None

    customer_details = obj.get("customer_details") or {}
    email = customer_details.get("email") or obj.get("customer_email")

    if not email:
        return None

    return email.strip().lower()


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

    event_id = (event.get("id") or "").strip()
    event_type = (event.get("type") or "").strip()
    obj = ((event.get("data") or {}).get("object")) or {}
    metadata = obj.get("metadata") or {}

    # --- helpers ---
    def _find_pmc(db: Session) -> Optional[PMC]:
        pmc_id = (metadata.get("pmc_id") or "").strip() or None
        customer_id = obj.get("customer")
        subscription_id = obj.get("subscription") or obj.get("id")  # sub object uses id
        email_l = _get_email_from_session(obj)

        pmc: Optional[PMC] = None

        # 1) metadata pmc_id (preferred)
        if pmc_id:
            try:
                pmc = db.query(PMC).filter(PMC.id == int(pmc_id)).first()
            except Exception:
                pmc = None

        # 2) match on customer id
        if pmc is None and customer_id:
            pmc = db.query(PMC).filter(PMC.stripe_customer_id == customer_id).first()

        # 3) match on subscription id
        if pmc is None and subscription_id and isinstance(subscription_id, str) and subscription_id.startswith("sub_"):
            pmc = db.query(PMC).filter(PMC.stripe_subscription_id == subscription_id).first()

        # 4) fallback: match by email
        if pmc is None and email_l:
            pmc = (
                db.query(PMC)
                .filter(func.lower(PMC.email) == email_l)
                .order_by(PMC.id.desc())
                .first()
            )

        return pmc

    # Ignore everything we don't care about
    HANDLED = {
        # Checkout completion (activation + upgrades)
        "checkout.session.completed",

        # Payment issues
        "invoice.payment_failed",
        "invoice.payment_action_required",

        # Subscription lifecycle
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }
    if event_type not in HANDLED:
        return JSONResponse({"ok": True})

    db: Session = SessionLocal()
    try:
        # NOTE: We find PMC for BOTH flows:
        # - PMC signup checkout (metadata contains pmc_id/type)
        # - Upgrade checkout (metadata contains pmc_id/type)
        pmc = _find_pmc(db)
        if pmc is None:
            print("[stripe_webhook] No PMC matched. event=", event_type, "event_id=", event_id)
            return JSONResponse({"ok": True})

        # ✅ Idempotency by Stripe event id (best practice)
        last_event = getattr(pmc, "last_stripe_event_id", None)
        if last_event and last_event == event_id:
            return JSONResponse({"ok": True})
        _set_if_attr(pmc, "last_stripe_event_id", event_id)

        # ------------------------------------------------------------------
        # 1) checkout.session.completed (activation moment + upgrade purchases)
        # ------------------------------------------------------------------
        if event_type == "checkout.session.completed":
            checkout_type = (metadata.get("type") or "").strip()

            # ===============================================================
            # A) Upgrade purchase flow (guest checkout on connected account)
            # ===============================================================
            if checkout_type == "upgrade_purchase":
                purchase_id = (metadata.get("purchase_id") or "").strip()
                checkout_session_id = (obj.get("id") or "").strip()
                payment_intent_id = obj.get("payment_intent")

                if purchase_id:
                    # Import here to avoid any potential circular import issues
                    try:
                        from models import UpgradePurchase  # you must create this model/table
                    except Exception as e:
                        # If model not available yet, don't fail the webhook
                        print("[stripe_webhook] UpgradePurchase model missing:", e)
                        db.commit()
                        return JSONResponse({"ok": True})

                    try:
                        p = (
                            db.query(UpgradePurchase)
                            .filter(UpgradePurchase.id == int(purchase_id))
                            .first()
                        )
                    except Exception:
                        p = None

                    if p:
                        # Idempotent update
                        if getattr(p, "status", None) != "paid":
                            p.status = "paid"
                            p.paid_at = datetime.now(timezone.utc)

                        # Persist stripe ids if columns exist / empty
                        if hasattr(p, "stripe_checkout_session_id") and checkout_session_id:
                            if not getattr(p, "stripe_checkout_session_id", None):
                                p.stripe_checkout_session_id = checkout_session_id

                        if hasattr(p, "stripe_payment_intent_id") and payment_intent_id:
                            if not getattr(p, "stripe_payment_intent_id", None):
                                p.stripe_payment_intent_id = payment_intent_id

                        db.commit()

                # Important: return early so we don't run PMC signup logic
                return JSONResponse({"ok": True})

            # ===============================================================
            # B) Existing PMC billing / activation flow
            # ===============================================================
            # Support both your new + legacy type names
            ALLOWED_TYPES = {
                "pmc_full_activation",                     # ✅ your current flow
                "pmc_setup_plus_property_subscription",     # legacy name
                "pmc_signup_onetime",
                "pmc_property_subscription",
            }
            if checkout_type and checkout_type not in ALLOWED_TYPES:
                db.commit()
                return JSONResponse({"ok": True})

            customer_id = obj.get("customer")
            checkout_session_id = obj.get("id")
            subscription_id = obj.get("subscription")  # exists for subscription mode

            # Always record Stripe IDs when present
            if customer_id:
                _set_if_attr(pmc, "stripe_customer_id", customer_id)
            if subscription_id:
                _set_if_attr(pmc, "stripe_subscription_id", subscription_id)

            # Store last checkout session (if your model has it)
            _set_if_attr(pmc, "last_stripe_checkout_session_id", checkout_session_id)

            # Mark active/paid for combined flow
            if checkout_type in {"pmc_full_activation", "pmc_setup_plus_property_subscription"}:
                _set_if_attr(pmc, "billing_status", "active")
                _set_if_attr(pmc, "active", True)
                _set_if_attr(pmc, "sync_enabled", True)
                _set_if_attr(pmc, "signup_paid_at", datetime.now(timezone.utc))

            # Old one-time setup flow
            if checkout_type == "pmc_signup_onetime":
                _set_if_attr(pmc, "billing_status", "active")
                _set_if_attr(pmc, "active", True)
                _set_if_attr(pmc, "sync_enabled", True)
                _set_if_attr(pmc, "signup_paid_at", datetime.now(timezone.utc))
                _set_if_attr(pmc, "stripe_signup_checkout_session_id", checkout_session_id)

        # ------------------------------------------------------------------
        # 2) invoice.payment_failed / payment_action_required (past_due)
        # ------------------------------------------------------------------
        elif event_type in {"invoice.payment_failed", "invoice.payment_action_required"}:
            _set_if_attr(pmc, "billing_status", "past_due")
            _set_if_attr(pmc, "active", False)

        # ------------------------------------------------------------------
        # 3) subscription.deleted (canceled)
        # ------------------------------------------------------------------
        elif event_type == "customer.subscription.deleted":
            _set_if_attr(pmc, "billing_status", "canceled")
            _set_if_attr(pmc, "active", False)

        # ------------------------------------------------------------------
        # 4) subscription.updated (recovery/cancel_at_period_end/etc.)
        # ------------------------------------------------------------------
        elif event_type == "customer.subscription.updated":
            status = (obj.get("status") or "").lower()

            if status in {"active", "trialing"}:
                _set_if_attr(pmc, "billing_status", "active")
                _set_if_attr(pmc, "active", True)
            elif status in {"past_due", "unpaid", "incomplete", "incomplete_expired"}:
                _set_if_attr(pmc, "billing_status", "past_due")
                _set_if_attr(pmc, "active", False)
            elif status in {"canceled"}:
                _set_if_attr(pmc, "billing_status", "canceled")
                _set_if_attr(pmc, "active", False)

        db.commit()
        return JSONResponse({"ok": True})

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
