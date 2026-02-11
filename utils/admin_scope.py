# utils/admin_scope.py

from __future__ import annotations

import os
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func

from models import PMC, PMCUser  # adjust if your import path differs

ADMIN_IDENTITY_SESSION_KEY = os.getenv("ADMIN_IDENTITY_SESSION_KEY", "admin_email")


def get_current_admin_identity(request) -> Optional[str]:
    # 1) Auth middleware scope user
    try:
        user = request.scope.get("user")
        if user and getattr(user, "is_authenticated", False):
            for attr in ("email", "username", "name"):
                val = getattr(user, attr, None)
                if val and str(val).strip():
                    return str(val).strip().lower()
    except Exception:
        pass

    # 2) Explicit session key
    try:
        sess_val = request.session.get(ADMIN_IDENTITY_SESSION_KEY)
        if sess_val and str(sess_val).strip():
            return str(sess_val).strip().lower()
    except Exception:
        pass

    # 3) Existing Google login flow: session["user"]["email"]
    try:
        sess_user = request.session.get("user")
        if isinstance(sess_user, dict):
            email = (sess_user.get("email") or "").strip()
            if email:
                return email.lower()
    except Exception:
        pass

    # 4) Header fallback
    try:
        hdr = request.headers.get("x-admin-email") or request.headers.get("x-admin-user")
        if hdr and hdr.strip():
            return hdr.strip().lower()
    except Exception:
        pass

    return None


def is_super_admin(email: Optional[str]) -> bool:
    if not email:
        return False

    allow = os.getenv("ADMIN_EMAILS", "")
    if allow.strip():
        allowed = {e.strip().lower() for e in allow.split(",") if e.strip()}
        return email.lower() in allowed

    # fallback
    return email.lower() in {"corbett.jarrod@gmail.com"}


def get_user_role_and_scope(request, db: Session):
    """
    Returns:
      (user_role, pmc_obj, pmc_user, billing_status, needs_payment)

    - user_role: "super" | "pmc"
    - pmc_obj: PMC | None
    - pmc_user: PMCUser | None
    - billing_status: "active" | "pending" | "past_due" | ... | None
    - needs_payment: bool
    """
    email = get_current_admin_identity(request)

    if is_super_admin(email):
        return "super", None, None, None, False

    if not email:
        return "pmc", None, None, None, False

    email_l = (email or "").strip().lower()

    # 1) Prefer explicit PMCUser membership
    pmc_user = (
        db.query(PMCUser)
        .filter(func.lower(PMCUser.email) == email_l, PMCUser.is_active == True)
        .first()
    )

    # DB-driven superuser override
    if pmc_user and bool(getattr(pmc_user, "is_superuser", False)):
        return "super", None, pmc_user, None, False

    pmc = None
    if pmc_user:
        pmc = db.query(PMC).filter(PMC.id == pmc_user.pmc_id).first()
    else:
        # 2) Fallback: PMC owner email on PMC table
        pmc = db.query(PMC).filter(func.lower(PMC.email) == email_l).first()

    if not pmc:
        return "pmc", None, None, None, False

    # Billing gating
    billing_status = (getattr(pmc, "billing_status", None) or "pending").strip().lower()
    is_paid_and_active = (billing_status == "active") and bool(getattr(pmc, "active", False))
    needs_payment = not is_paid_and_active

    return "pmc", pmc, pmc_user, billing_status, needs_payment
