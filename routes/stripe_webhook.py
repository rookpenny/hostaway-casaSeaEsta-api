# routes/stripe_webhook.py
import os
import stripe
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from database import get_db
from models import PMC, PMCUser

router = APIRouter()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

@router.post("/stripe/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    if not WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Missing STRIPE_WEBHOOK_SECRET")

    payload = await request.body()
    sig = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig, WEBHOOK_SECRET)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook error: {e}")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]

        pmc_id = (session.get("metadata") or {}).get("pmc_id")
        if not pmc_id:
            return JSONResponse({"ok": True})

        pmc = db.query(PMC).filter(PMC.id == int(pmc_id)).first()
        if not pmc:
            return JSONResponse({"ok": True})

        # Activate PMC
        pmc.billing_status = "active"
        pmc.active = True
        pmc.sync_enabled = True
        pmc.signup_paid_at = __import__("datetime").datetime.utcnow()
        pmc.stripe_customer_id = session.get("customer")
        pmc.stripe_subscription_id = session.get("subscription")

        # Create/ensure PMC admin user record (so their Google email can log in)
        email_l = (pmc.email or "").strip().lower()
        if email_l:
            existing = (
                db.query(PMCUser)
                .filter(PMCUser.pmc_id == pmc.id, PMCUser.email == email_l)
                .first()
            )
            if not existing:
                db.add(PMCUser(
                    pmc_id=pmc.id,
                    email=email_l,
                    full_name=pmc.main_contact,
                    role="admin",
                    is_active=True,
                ))

        db.commit()

    return JSONResponse({"ok": True})
