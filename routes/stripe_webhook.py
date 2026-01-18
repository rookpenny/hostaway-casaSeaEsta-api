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
from models import PMC, UpgradePurchase  # ✅ make sure UpgradePurchase exists in models.py

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
    """Set an attribute only if it exists (backward compatible)."""
    if hasattr(obj, attr):
        setattr(obj, attr, value)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_email_from_session(obj: dict) -> Optional[str]:
    """Extract normalized email from Stripe Checkout session object."""
    if not isinstance(obj, dict):
        return None
    customer_details = obj.get("customer_details") or {}
    email = customer_details.get("email") or obj.get("customer_email")
    return email.strip().lower() if email else None


def _safe_int(x) -> Optional[int]:
    try:
        if x is None:
            return None
        return int(str(x).strip())
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

    event_id = (event.get("id") or "").strip()
    event_type = (event.get("type") or "").strip()
    obj = ((event.get("data") or {}).get("object")) or {}
    metadata = obj.get("metadata") or {}

    # ------------------------------------------------------------
    # Find PMC (subscription / signup events)
    # ------------------------------------------------------------
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

    # ------------------------------------------------------------
    # Find UpgradePurchase (upgrade payment events)
    # ------------------------------------------------------------
    def _find_purchase(db: Session) -> Optional[UpgradePurchase]:
        """
        Preferred: metadata.purchase_id (we set this when creating Checkout Session)
        Fallback: stripe_checkout_session_id (for checkout.session.completed)
        Fallback: stripe_payment_intent_id (for charge.refunded)
        """
        purchase_id = _safe_int(metadata.get("purchase_id"))
        if purchase_id:
            p = db.query(UpgradePurchase).filter(UpgradePurchase.id == purchase_id).first()
            if p:
                return p

        # checkout.session.completed includes session id
        sess_id = obj.get("id")
        if isinstance(sess_id, str) and sess_id.startswith("cs_"):
            p = (
                db.query(UpgradePurchase)
                .filter(UpgradePurchase.stripe_checkout_session_id == sess_id)
                .first()
            )
            if p:
                return p

        # charge.refunded includes payment_intent
        pi = obj.get("payment_intent")
        if isinstance(pi, str) and pi.startswith("pi_"):
            p = (
                db.query(UpgradePurchase)
                .filter(UpgradePurchase.stripe_payment_intent_id == pi)
                .first()
            )
            if p:
                return p

        return None

    # ------------------------------------------------------------
    # Events we handle
    # ------------------------------------------------------------
    HANDLED = {
        # PMC billing (existing)
        "checkout.session.completed",
        "invoice.payment_failed",
        "invoice.payment_action_required",
        "customer.subscription.updated",
        "customer.subscription.deleted",

        # ✅ Upgrade refunds
        "charge.refunded",
    }

    if event_type not in HANDLED:
        return JSONResponse({"ok": True})

    db: Session = SessionLocal()
    try:
        # ============================================================
        # A) Upgrade purchase PAID (checkout.session.completed)
        # ============================================================
        if event_type == "checkout.session.completed":
            # This event fires for BOTH PMC signup flows and upgrade purchases.
            checkout_type = (metadata.get("type") or "").strip()

            # --------------------------
            # Upgrade purchase flow
            # --------------------------
            if checkout_type == "upgrade_purchase":
                purchase = _find_purchase(db)
                if not purchase:
                    print("[stripe_webhook] No UpgradePurchase matched. event_id=", event_id)
                    return JSONResponse({"ok": True})

                # Idempotency: if already paid, do nothing
                if (getattr(purchase, "status", "") or "").lower() == "paid":
                    return JSONResponse({"ok": True})

                # session id + payment_intent from Checkout Session
                session_id = obj.get("id")
                payment_intent_id = obj.get("payment_intent")

                if session_id and not getattr(purchase, "stripe_checkout_session_id", None):
                    purchase.stripe_checkout_session_id = session_id

                if payment_intent_id and not getattr(purchase, "stripe_payment_intent_id", None):
                    purchase.stripe_payment_intent_id = payment_intent_id

                purchase.status = "paid"
                purchase.paid_at = _now()

                db.commit()
                return JSONResponse({"ok": True})

            # --------------------------
            # PMC signup/subscription flow (your existing logic)
            # --------------------------
            pmc = _find_pmc(db)
            if pmc is None:
                return JSONResponse({"ok": True})

            # ✅ Idempotency by Stripe event id (best practice)
            last_event = getattr(pmc, "last_stripe_event_id", None)
            if last_event and last_event == event_id:
                return JSONResponse({"ok": True})
            _set_if_attr(pmc, "last_stripe_event_id", event_id)

            ALLOWED_TYPES = {
                "pmc_full_activation",
                "pmc_setup_plus_property_subscription",
                "pmc_signup_onetime",
                "pmc_property_subscription",
            }
            if checkout_type and checkout_type not in ALLOWED_TYPES:
                db.commit()
                return JSONResponse({"ok": True})

            customer_id = obj.get("customer")
            checkout_session_id = obj.get("id")
            subscription_id = obj.get("subscription")  # exists for subscription mode

            if customer_id:
                _set_if_attr(pmc, "stripe_customer_id", customer_id)
            if subscription_id:
                _set_if_attr(pmc, "stripe_subscription_id", subscription_id)

            _set_if_attr(pmc, "last_stripe_checkout_session_id", checkout_session_id)

            if checkout_type in {"pmc_full_activation", "pmc_setup_plus_property_subscription"}:
                _set_if_attr(pmc, "billing_status", "active")
                _set_if_attr(pmc, "active", True)
                _set_if_attr(pmc, "sync_enabled", True)
                _set_if_attr(pmc, "signup_paid_at", _now())

            if checkout_type == "pmc_signup_onetime":
                _set_if_attr(pmc, "billing_status", "active")
                _set_if_attr(pmc, "active", True)
                _set_if_attr(pmc, "sync_enabled", True)
                _set_if_attr(pmc, "signup_paid_at", _now())
                _set_if_attr(pmc, "stripe_signup_checkout_session_id", checkout_session_id)

            db.commit()
            return JSONResponse({"ok": True})

        # ============================================================
        # B) Upgrade purchase REFUNDED (charge.refunded)
        # ============================================================
        if event_type == "charge.refunded":
            # obj is a Charge
            charge_id = obj.get("id")
            payment_intent_id = obj.get("payment_intent")
            amount_refunded = int(obj.get("amount_refunded") or 0)
            refunded = bool(obj.get("refunded"))

            purchase = _find_purchase(db)

            # If metadata wasn't on the charge, but we have PI, still works if we stored PI on paid.
            if not purchase and isinstance(payment_intent_id, str) and payment_intent_id.startswith("pi_"):
                purchase = (
                    db.query(UpgradePurchase)
                    .filter(UpgradePurchase.stripe_payment_intent_id == payment_intent_id)
                    .first()
                )

            if not purchase:
                print("[stripe_webhook] No UpgradePurchase matched for refund. charge=", charge_id, "pi=", payment_intent_id)
                return JSONResponse({"ok": True})

            # Store PI if missing
            if payment_intent_id and not getattr(purchase, "stripe_payment_intent_id", None):
                purchase.stripe_payment_intent_id = payment_intent_id

            # Idempotency-ish: if we already recorded same refunded amount, do nothing
            prev_refunded = int(getattr(purchase, "refunded_amount_cents", 0) or 0)
            if amount_refunded <= prev_refunded and (getattr(purchase, "status", "") or "").lower() == "refunded":
                return JSONResponse({"ok": True})

            purchase.refunded_amount_cents = max(prev_refunded, amount_refunded)

            # If fully refunded, mark refunded. If partial, you can choose "refunded" or "partial_refund"
            # Your table comment says pending|paid|refunded|disputed|failed — so we’ll keep "refunded"
            # and rely on refunded_amount_cents for partials.
            if refunded or amount_refunded >= int(getattr(purchase, "amount_cents", 0) or 0):
                purchase.status = "refunded"
                purchase.refunded_at = _now()
            else:
                # partial refund: keep paid but set refunded fields (or set refunded anyway)
                # I recommend: set to "refunded" if any refund, since you track refunded_amount_cents.
                purchase.status = "refunded"
                purchase.refunded_at = _now()

            db.commit()
            return JSONResponse({"ok": True})

        # ============================================================
        # Existing PMC lifecycle events (unchanged)
        # ============================================================
        pmc = _find_pmc(db)
        if pmc is None:
            return JSONResponse({"ok": True})

        # Idempotency by event id on PMC
        last_event = getattr(pmc, "last_stripe_event_id", None)
        if last_event and last_event == event_id:
            return JSONResponse({"ok": True})
        _set_if_attr(pmc, "last_stripe_event_id", event_id)

        if event_type in {"invoice.payment_failed", "invoice.payment_action_required"}:
            _set_if_attr(pmc, "billing_status", "past_due")
            _set_if_attr(pmc, "active", False)

        elif event_type == "customer.subscription.deleted":
            _set_if_attr(pmc, "billing_status", "canceled")
            _set_if_attr(pmc, "active", False)

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
