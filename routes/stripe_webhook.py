# routes/stripe_webhook.py
import os
from datetime import datetime, timezone
from typing import Optional, Tuple, Any, Dict, List

import stripe
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import SessionLocal

from models import PMC, UpgradePurchase, Property, Upgrade, ChatSession, PMCUser, PMCMessage, Notification
from utils.emailer import send_upgrade_purchase_email


router = APIRouter()


# ----------------------------
# Env + small helpers
# ----------------------------
def _load_env() -> Tuple[str, str]:
    stripe_secret = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    webhook_secret = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
    return stripe_secret, webhook_secret


def _require_env() -> Tuple[str, str]:
    stripe_secret, webhook_secret = _load_env()

    missing = []
    if not stripe_secret:
        missing.append("STRIPE_SECRET_KEY")
    if not webhook_secret:
        missing.append("STRIPE_WEBHOOK_SECRET")

    if missing:
        raise HTTPException(status_code=500, detail=f"Missing env vars: {', '.join(missing)}")

    return stripe_secret, webhook_secret


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_int(x) -> Optional[int]:
    try:
        if x is None:
            return None
        return int(str(x).strip())
    except Exception:
        return None


def _set_if_attr(obj: Any, attr: str, value: Any) -> None:
    """Set an attribute only if it exists (backward compatible)."""
    if hasattr(obj, attr):
        setattr(obj, attr, value)


def _get_email_from_session(obj: Dict[str, Any]) -> Optional[str]:
    if not isinstance(obj, dict):
        return None
    customer_details = obj.get("customer_details") or {}
    email = customer_details.get("email") or obj.get("customer_email")
    return email.strip().lower() if email else None


# ----------------------------
# Lookup helpers
# ----------------------------


def _pref_allows_upgrade_email(prefs: Any) -> bool:
    """
    prefs is JSONB; missing/invalid => allow by default.
    key: notification_prefs["upgrade_purchases_email"] (default True)
    """
    try:
        if not isinstance(prefs, dict):
            return True
        return bool(prefs.get("upgrade_purchases_email", True))
    except Exception:
        return True


def _select_upgrade_recipients(db: Session, pmc: PMC) -> List[str]:
    """
    1) active pmc_users with role owner/admin or is_superuser, prefs allow
    2) fallback pmc.email
    """
    recipients: list[str] = []

    users = (
        db.query(PMCUser)
        .filter(PMCUser.pmc_id == pmc.id, PMCUser.is_active == True)  # noqa: E712
        .all()
    )

    for u in users:
        role = (getattr(u, "role", "") or "").lower().strip()
        is_super = bool(getattr(u, "is_superuser", False))

        if not (is_super or role in {"owner", "admin"}):
            continue

        if not _pref_allows_upgrade_email(getattr(u, "notification_prefs", None)):
            continue

        em = (getattr(u, "email", "") or "").strip().lower()
        if em:
            recipients.append(em)

    if not recipients:
        em = (getattr(pmc, "email", "") or "").strip().lower()
        if em:
            recipients.append(em)

    # de-dupe, keep order
    out = []
    seen = set()
    for e in recipients:
        if e not in seen:
            out.append(e)
            seen.add(e)
    return out


def _upsert_pmc_message(
    db: Session,
    *,
    pmc_id: int,
    dedupe_key: str,
    msg_type: str,
    subject: str,
    body: str,
    status: str = "open",     # open|resolved
    severity: str = "info",   # info|warning|critical
    purchase: Optional[UpgradePurchase] = None,
) -> None:
    q = db.query(PMCMessage).filter(PMCMessage.pmc_id == pmc_id, PMCMessage.dedupe_key == dedupe_key)
    m = q.first()

    if m:
        m.type = msg_type
        m.subject = subject
        m.body = body
        if hasattr(m, "status"):
            m.status = status
        if hasattr(m, "severity"):
            m.severity = severity
        m.is_read = False
        # keep links to entities up to date
        if purchase:
            m.property_id = getattr(purchase, "property_id", None)
            m.upgrade_purchase_id = int(purchase.id)
            m.upgrade_id = getattr(purchase, "upgrade_id", None)
            m.guest_session_id = getattr(purchase, "guest_session_id", None)
        db.add(m)
        return

    m = PMCMessage(
        pmc_id=pmc_id,
        dedupe_key=dedupe_key,
        type=msg_type,
        subject=subject,
        body=body,
        is_read=False,
        property_id=(getattr(purchase, "property_id", None) if purchase else None),
        upgrade_purchase_id=(int(purchase.id) if purchase else None),
        upgrade_id=(getattr(purchase, "upgrade_id", None) if purchase else None),
        guest_session_id=(getattr(purchase, "guest_session_id", None) if purchase else None),
    )
    if hasattr(m, "status"):
        m.status = status
    if hasattr(m, "severity"):
        m.severity = severity

    db.add(m)


def _resolve_pmc_message(db: Session, *, pmc_id: int, dedupe_key: str) -> None:
    m = (
        db.query(PMCMessage)
        .filter(PMCMessage.pmc_id == pmc_id, PMCMessage.dedupe_key == dedupe_key)
        .first()
    )
    if not m:
        return
    if hasattr(m, "status"):
        m.status = "resolved"
    db.add(m)


def _notify_pmc_upgrade_purchase(db: Session, purchase: UpgradePurchase) -> None:
    """
    Creates admin message + emails recipients.
    Email only sends on first message creation (prevents duplicate emails).
    """
    pmc = db.query(PMC).filter(PMC.id == int(purchase.pmc_id)).first()
    if not pmc:
        return

    prop = db.query(Property).filter(Property.id == int(purchase.property_id)).first()
    upgrade = db.query(Upgrade).filter(Upgrade.id == int(purchase.upgrade_id)).first()

    sess = None
    if getattr(purchase, "guest_session_id", None):
        sess = db.query(ChatSession).filter(ChatSession.id == int(purchase.guest_session_id)).first()

    pmc_name = (getattr(pmc, "pmc_name", "") or "PMC").strip()
    property_name = (getattr(prop, "property_name", "") or f"Property {purchase.property_id}").strip()
    upgrade_title = (getattr(upgrade, "title", "") or "Upgrade").strip()

    guest_name = (getattr(sess, "guest_name", None) or "").strip() if sess else ""
    arr = (getattr(sess, "arrival_date", None) or "").strip() if sess else ""
    dep = (getattr(sess, "departure_date", None) or "").strip() if sess else ""

    amount_cents = int(getattr(purchase, "amount_cents", 0) or 0)
    currency = (getattr(purchase, "currency", None) or "usd").strip()

    subject = f"Upgrade purchased: {upgrade_title} — {property_name}"

    body_lines = [
        f"Upgrade: {upgrade_title}",
        f"Property: {property_name}",
        f"Amount: {amount_cents/100:.2f} {currency.upper()}",
        f"Purchase ID: {purchase.id}",
    ]
    if guest_name:
        body_lines.insert(0, f"Guest: {guest_name}")
    if arr and dep:
        body_lines.append(f"Stay: {arr} → {dep}")

    body = "\n".join(body_lines)

    dedupe_paid = f"upgrade_purchase:paid:{int(purchase.id)}"

    # ✅ determine if this is the first time we create the "paid" message
    existed_before = (
        db.query(PMCMessage)
        .filter(PMCMessage.pmc_id == int(pmc.id), PMCMessage.dedupe_key == dedupe_paid)
        .first()
        is not None
    )

    # ✅ upsert the paid message (idempotent)
    _upsert_pmc_message(
        db,
        pmc_id=int(pmc.id),
        dedupe_key=dedupe_paid,
        msg_type="upgrade_purchase_paid",
        subject=subject,
        body=body,
        severity="info",
        status="open",
        purchase=purchase,
    )
    db.commit()

    # ✅ email only on first creation
    if not existed_before:
        recipients = _select_upgrade_recipients(db, pmc)
        if recipients:
            try:
                send_upgrade_purchase_email(
                    recipients=recipients,
                    pmc_name=pmc_name,
                    property_name=property_name,
                    upgrade_title=upgrade_title,
                    amount_cents=amount_cents,
                    currency=currency,
                    purchase_id=int(purchase.id),
                    guest_name=(guest_name or None),
                    arrival_date=(arr or None),
                    departure_date=(dep or None),
                )
            except Exception:
                # best-effort only
                pass

def _notify_inapp_upgrade_purchase(db: Session, purchase: UpgradePurchase) -> None:
    """
    Creates in-app Notifications (NOT PMCMessage) for upgrade purchases.
    Goes to active owner/admin (and superusers) on the PMC team.
    Idempotency is handled by the webhook logic (status + last_stripe_event_id).
    """
    pmc = db.query(PMC).filter(PMC.id == int(purchase.pmc_id)).first()
    if not pmc:
        return

    prop = db.query(Property).filter(Property.id == int(purchase.property_id)).first()
    upgrade = db.query(Upgrade).filter(Upgrade.id == int(purchase.upgrade_id)).first()
    sess = None
    if getattr(purchase, "guest_session_id", None):
        sess = db.query(ChatSession).filter(ChatSession.id == int(purchase.guest_session_id)).first()

    property_name = (getattr(prop, "property_name", "") or f"Property {purchase.property_id}").strip()
    upgrade_title = (getattr(upgrade, "title", "") or "Upgrade").strip()
    guest_name = ((getattr(sess, "guest_name", None) or "").strip() if sess else "")

    amount_cents = int(getattr(purchase, "amount_cents", 0) or 0)
    currency = (getattr(purchase, "currency", None) or "usd").strip().upper()

    title = "Upgrade purchased"
    body = f"{upgrade_title} • {property_name} • {amount_cents/100:.2f} {currency}"
    if guest_name:
        body = f"{guest_name} • {body}"

    # Notify active owner/admin + superusers
    users = (
        db.query(PMCUser)
        .filter(PMCUser.pmc_id == int(pmc.id), PMCUser.is_active == True)  # noqa: E712
        .all()
    )

    for u in users:
        role = (getattr(u, "role", "") or "").lower().strip()
        is_super = bool(getattr(u, "is_superuser", False))
        if not (is_super or role in {"owner", "admin"}):
            continue

        db.add(
            Notification(
                pmc_id=int(pmc.id),
                user_id=int(u.id),
                type="upgrade_purchased",
                title=title,
                body=body,
                meta={
                    "upgrade_purchase_id": int(purchase.id),
                    "property_id": int(getattr(purchase, "property_id", 0) or 0) or None,
                    "upgrade_id": int(getattr(purchase, "upgrade_id", 0) or 0) or None,
                    "guest_session_id": int(getattr(purchase, "guest_session_id", 0) or 0) or None,
                },
                is_read=False,
                created_at=datetime.utcnow(),  # keep naive UTC consistent with the rest of the app
            )
        )


def _find_pmc_from_event(db: Session, obj: Dict[str, Any], metadata: Dict[str, Any]) -> Optional[PMC]:
    """
    Used for PMC billing flows only.
    Prefers metadata.pmc_id, then stripe_customer_id, then subscription_id, then email.
    """
    pmc_id = (metadata.get("pmc_id") or "").strip() or None
    customer_id = obj.get("customer")
    subscription_id = obj.get("subscription") or obj.get("id")
    email_l = _get_email_from_session(obj)

    pmc: Optional[PMC] = None

    if pmc_id:
        try:
            pmc = db.query(PMC).filter(PMC.id == int(pmc_id)).first()
        except Exception:
            pmc = None

    if pmc is None and customer_id:
        pmc = db.query(PMC).filter(PMC.stripe_customer_id == customer_id).first()

    if (
        pmc is None
        and subscription_id
        and isinstance(subscription_id, str)
        and subscription_id.startswith("sub_")
    ):
        pmc = db.query(PMC).filter(PMC.stripe_subscription_id == subscription_id).first()

    if pmc is None and email_l:
        pmc = (
            db.query(PMC)
            .filter(func.lower(PMC.email) == email_l)
            .order_by(PMC.id.desc())
            .first()
        )

    return pmc


def _find_purchase_strict(db: Session, metadata: Dict[str, Any]) -> Optional[UpgradePurchase]:
    """
    ✅ IMPORTANT: For upgrades, ONLY trust metadata.purchase_id.
    This prevents accidentally marking multiple rows as paid.
    """
    purchase_id = _safe_int(metadata.get("purchase_id"))
    if not purchase_id:
        return None
    return db.query(UpgradePurchase).filter(UpgradePurchase.id == purchase_id).first()


def _find_purchase_fallback(db: Session, obj: Dict[str, Any]) -> Optional[UpgradePurchase]:
    """
    Fallback ONLY for edge cases where metadata was missing.
    (Still safe because these IDs are unique.)
    """
    sess_id = obj.get("id")
    if isinstance(sess_id, str) and sess_id.startswith("cs_"):
        p = (
            db.query(UpgradePurchase)
            .filter(UpgradePurchase.stripe_checkout_session_id == sess_id)
            .first()
        )
        if p:
            return p

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
    metadata = (obj.get("metadata") or {}) if isinstance(obj, dict) else {}

    HANDLED = {
        # PMC billing
        "checkout.session.completed",
        "invoice.payment_failed",
        "invoice.payment_action_required",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        # Upgrade lifecycle extras
        "checkout.session.expired",
        "payment_intent.payment_failed",
        # Upgrade refunds
        "charge.refunded",
        # Optional reconciliation
        "transfer.created",
    }

    if event_type not in HANDLED:
        return JSONResponse({"ok": True})

    db: Session = SessionLocal()
    try:
        # ============================================================
        # 1) Transfer created (best-effort reconciliation)
        # ============================================================
        if event_type == "transfer.created":
            transfer_id = obj.get("id")
            transfer_metadata = obj.get("metadata") or {}

            purchase_id = _safe_int(transfer_metadata.get("purchase_id"))
            if not purchase_id:
                return JSONResponse({"ok": True})

            purchase = db.query(UpgradePurchase).filter(UpgradePurchase.id == purchase_id).first()
            if not purchase:
                return JSONResponse({"ok": True})

            if getattr(purchase, "stripe_transfer_id", None) == transfer_id:
                return JSONResponse({"ok": True})

            if transfer_id:
                _set_if_attr(purchase, "stripe_transfer_id", transfer_id)

            dest_acct = obj.get("destination")
            if dest_acct:
                _set_if_attr(purchase, "stripe_destination_account_id", dest_acct)

            db.commit()
            return JSONResponse({"ok": True})

        # ============================================================
        # 2) Checkout completed (PMC OR Upgrade)
        # ============================================================
        if event_type == "checkout.session.completed":
            checkout_type = (metadata.get("type") or "").strip()

            # --------------------------
            # Upgrade purchase flow
            # --------------------------
            if checkout_type == "upgrade_purchase":
                purchase = _find_purchase_strict(db, metadata)
                if not purchase:
                    purchase = _find_purchase_fallback(db, obj)

                if not purchase:
                    return JSONResponse({"ok": True})

                # ✅ idempotency: Stripe may resend same event
                if getattr(purchase, "last_stripe_event_id", None) == event_id:
                    return JSONResponse({"ok": True})

                current_status = (getattr(purchase, "status", "") or "").lower()
                if current_status in {"paid", "refunded"}:
                    # still store event id so replays short-circuit next time
                    _set_if_attr(purchase, "last_stripe_event_id", event_id)
                    db.commit()
                    return JSONResponse({"ok": True})

                session_id = obj.get("id")
                payment_intent_id = obj.get("payment_intent")

                if session_id:
                    _set_if_attr(purchase, "stripe_checkout_session_id", session_id)
                if payment_intent_id:
                    _set_if_attr(purchase, "stripe_payment_intent_id", payment_intent_id)

                amount = int(getattr(purchase, "amount_cents", 0) or 0)
                fee = int(getattr(purchase, "platform_fee_cents", 0) or 0)
                net = max(0, amount - max(0, fee))
                _set_if_attr(purchase, "net_amount_cents", net)

                purchase.status = "paid"
                _set_if_attr(purchase, "paid_at", _now())
                _set_if_attr(purchase, "last_stripe_event_id", event_id)

                # ✅ commit paid first (you wanted this)
                db.commit()

                # resolve “pending” message best-effort
                try:
                    _resolve_pmc_message(
                        db,
                        pmc_id=int(purchase.pmc_id),
                        dedupe_key=f"upgrade_purchase:pending:{int(purchase.id)}",
                    )
                    db.commit()
                except Exception:
                    db.rollback()

                # notify best-effort
                try:
                    _notify_pmc_upgrade_purchase(db, purchase)
                    _notify_inapp_upgrade_purchase(db, purchase) # ✅ NEW in-app Notification
                    
                except Exception:
                    pass

                return JSONResponse({"ok": True})

            # --------------------------
            # PMC subscription flow
            # --------------------------
            pmc = _find_pmc_from_event(db, obj, metadata)
            if pmc is None:
                return JSONResponse({"ok": True})

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
            subscription_id = obj.get("subscription")

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
        # 3) Upgrade payment failed (PaymentIntent)
        # ============================================================
        if event_type == "payment_intent.payment_failed":
            pi_id = obj.get("id")
            if not (isinstance(pi_id, str) and pi_id.startswith("pi_")):
                return JSONResponse({"ok": True})

            purchase = (
                db.query(UpgradePurchase)
                .filter(UpgradePurchase.stripe_payment_intent_id == pi_id)
                .first()
            )
            if not purchase:
                return JSONResponse({"ok": True})

            if getattr(purchase, "last_stripe_event_id", None) == event_id:
                return JSONResponse({"ok": True})

            status_now = (getattr(purchase, "status", "") or "").lower()
            if status_now in {"paid", "refunded"}:
                _set_if_attr(purchase, "last_stripe_event_id", event_id)
                db.commit()
                return JSONResponse({"ok": True})

            purchase.status = "failed"
            _set_if_attr(purchase, "last_stripe_event_id", event_id)

            _upsert_pmc_message(
                db,
                pmc_id=int(purchase.pmc_id),
                dedupe_key=f"upgrade_purchase:failed:{int(purchase.id)}",
                msg_type="upgrade_purchase_failed",
                subject="Upgrade payment failed",
                body=f"Upgrade payment failed. Purchase ID: {purchase.id}",
                severity="warning",
                status="open",
                purchase=purchase,
            )
            _resolve_pmc_message(db, pmc_id=int(purchase.pmc_id), dedupe_key=f"upgrade_purchase:pending:{int(purchase.id)}")
            db.commit()
            return JSONResponse({"ok": True})

        # ============================================================
        # 4) Upgrade checkout expired
        # ============================================================
        if event_type == "checkout.session.expired":
            sess_id = obj.get("id")
            if not (isinstance(sess_id, str) and sess_id.startswith("cs_")):
                return JSONResponse({"ok": True})

            purchase = (
                db.query(UpgradePurchase)
                .filter(UpgradePurchase.stripe_checkout_session_id == sess_id)
                .first()
            )
            if not purchase:
                return JSONResponse({"ok": True})

            if getattr(purchase, "last_stripe_event_id", None) == event_id:
                return JSONResponse({"ok": True})

            status_now = (getattr(purchase, "status", "") or "").lower()
            if status_now in {"paid", "refunded"}:
                _set_if_attr(purchase, "last_stripe_event_id", event_id)
                db.commit()
                return JSONResponse({"ok": True})

            purchase.status = "failed"
            _set_if_attr(purchase, "last_stripe_event_id", event_id)

            _upsert_pmc_message(
                db,
                pmc_id=int(purchase.pmc_id),
                dedupe_key=f"upgrade_purchase:failed:{int(purchase.id)}",
                msg_type="upgrade_purchase_failed",
                subject="Upgrade payment failed",
                body=f"Upgrade payment failed. Purchase ID: {purchase.id}",
                severity="warning",
                status="open",
                purchase=purchase,
            )
            _resolve_pmc_message(db, pmc_id=int(purchase.pmc_id), dedupe_key=f"upgrade_purchase:pending:{int(purchase.id)}")
            db.commit()
            return JSONResponse({"ok": True})

        # ============================================================
        # 5) Upgrade refunded (Charge)
        # ============================================================
        if event_type == "charge.refunded":
            payment_intent_id = obj.get("payment_intent")
            amount_refunded = int(obj.get("amount_refunded") or 0)
            refunded_flag = bool(obj.get("refunded"))

            purchase: Optional[UpgradePurchase] = None

            if isinstance(payment_intent_id, str) and payment_intent_id.startswith("pi_"):
                purchase = (
                    db.query(UpgradePurchase)
                    .filter(UpgradePurchase.stripe_payment_intent_id == payment_intent_id)
                    .first()
                )

            if not purchase:
                purchase = _find_purchase_fallback(db, obj)

            if not purchase:
                return JSONResponse({"ok": True})

            if getattr(purchase, "last_stripe_event_id", None) == event_id:
                return JSONResponse({"ok": True})

            prev_refunded = int(getattr(purchase, "refunded_amount_cents", 0) or 0)
            _set_if_attr(purchase, "refunded_amount_cents", max(prev_refunded, amount_refunded))

            if amount_refunded > 0 or refunded_flag:
                purchase.status = "refunded"
                _set_if_attr(purchase, "refunded_at", _now())

            _set_if_attr(purchase, "last_stripe_event_id", event_id)

            _upsert_pmc_message(
                db,
                pmc_id=int(purchase.pmc_id),
                dedupe_key=f"upgrade_purchase:refunded:{int(purchase.id)}",
                msg_type="upgrade_purchase_refunded",
                subject="Upgrade refunded",
                body=f"An upgrade was refunded. Purchase ID: {purchase.id}",
                severity="warning",
                status="open",
                purchase=purchase,
            )
            _resolve_pmc_message(db, pmc_id=int(purchase.pmc_id), dedupe_key=f"upgrade_purchase:pending:{int(purchase.id)}")
            db.commit()
            return JSONResponse({"ok": True})

        # ============================================================
        # 6) PMC lifecycle events
        # ============================================================
        pmc = _find_pmc_from_event(db, obj, metadata)
        if pmc is None:
            return JSONResponse({"ok": True})

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
            status_s = (obj.get("status") or "").lower()
            if status_s in {"active", "trialing"}:
                _set_if_attr(pmc, "billing_status", "active")
                _set_if_attr(pmc, "active", True)
            elif status_s in {"past_due", "unpaid", "incomplete", "incomplete_expired"}:
                _set_if_attr(pmc, "billing_status", "past_due")
                _set_if_attr(pmc, "active", False)
            elif status_s in {"canceled"}:
                _set_if_attr(pmc, "billing_status", "canceled")
                _set_if_attr(pmc, "active", False)

        db.commit()
        return JSONResponse({"ok": True})

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
