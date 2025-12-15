# routes/pmc_signup.py
import os

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from models import PMC, PMCUser

router = APIRouter()
templates = Jinja2Templates(directory="templates")


# ----------------------------
# Helpers
# ----------------------------
def _required_env() -> None:
    """
    New flow: we do NOT require Stripe env vars at signup time.
    (Stripe happens at the final billing step.)
    We only require APP_BASE_URL if your UI depends on it; otherwise optional.
    """
    # Keep this intentionally minimal to avoid blocking signup
    # You can still hard-require these in your billing checkout route.
    return


def _session_email(request: Request) -> str | None:
    user = request.session.get("user") or {}
    email = (user.get("email") or "").strip().lower()
    return email or None


def _session_name(request: Request) -> str | None:
    user = request.session.get("user") or {}
    name = (user.get("name") or "").strip()
    return name or None


def _set_if_attr(obj, attr: str, value) -> None:
    if hasattr(obj, attr):
        setattr(obj, attr, value)


# ----------------------------
# Pages
# ----------------------------
@router.get("/pmc/signup", response_class=HTMLResponse)
def pmc_signup_page(request: Request):
    _required_env()
    return templates.TemplateResponse(
        "pmc_signup.html",
        {
            "request": request,
            "pmc_name_prefill": "",
            "admin_name_prefill": _session_name(request) or "",
            "admin_email_prefill": _session_email(request) or "",
        },
    )


# ----------------------------
# Create PMC (NO STRIPE HERE in the new flow)
# ----------------------------
@router.post("/pmc/signup")
def pmc_signup_start(
    request: Request,
    pmc_name: str = Form(...),
    admin_name: str = Form(""),
    admin_email: str = Form(""),
    db: Session = Depends(get_db),
):
    """
    New flow:
    - Create/reuse PMC in "pending" state
    - Ensure owner PMCUser exists
    - Redirect to PMS connect step
    - Payment happens later, after PMS + property selection + billing review
    """
    pmc_name_clean = (pmc_name or "").strip()
    if not pmc_name_clean:
        raise HTTPException(status_code=400, detail="PMC name is required")

    # Prefer session email (Google login). Fall back to form.
    email_l = _session_email(request) or (admin_email or "").strip().lower()
    if not email_l:
        raise HTTPException(
            status_code=400,
            detail="Admin email is required (recommended: sign in first).",
        )

    admin_name_clean = (_session_name(request) or admin_name or "").strip() or None

    # --- Find or create PMC (latest by email wins) ---
    pmc = (
        db.query(PMC)
        .filter(func.lower(PMC.email) == email_l)
        .order_by(PMC.id.desc())
        .first()
    )

    if pmc:
        # Update the existing record but keep it in a locked/pending state until paid
        pmc.pmc_name = pmc_name_clean
        pmc.main_contact = admin_name_clean

        # "Paid at the end" → keep inactive until billing succeeds
        _set_if_attr(pmc, "billing_status", "pending")
        _set_if_attr(pmc, "active", False)
        _set_if_attr(pmc, "sync_enabled", False)

        db.commit()
        db.refresh(pmc)
    else:
        pmc = PMC(
            pmc_name=pmc_name_clean,
            email=email_l,
            main_contact=admin_name_clean,
            active=False,
            sync_enabled=False,
        )
        _set_if_attr(pmc, "billing_status", "pending")
        db.add(pmc)
        db.commit()
        db.refresh(pmc)

    # --- Ensure PMCUser exists (owner) ---
    existing_user = (
        db.query(PMCUser)
        .filter(
            PMCUser.pmc_id == pmc.id,
            func.lower(PMCUser.email) == email_l,
        )
        .first()
    )
    if not existing_user:
        pmc_user = PMCUser(
            pmc_id=pmc.id,
            email=email_l,
            full_name=admin_name_clean,
            role="owner",
            is_active=True,
        )
        db.add(pmc_user)
        db.commit()

    # ✅ Next step in new flow: connect PMS (no Stripe yet)
    return RedirectResponse("/pmc/onboarding/pms", status_code=303)


# ----------------------------
# Legacy success/cancel routes (optional)
# ----------------------------
@router.get("/pmc/signup/success", response_class=HTMLResponse)
def pmc_signup_success(request: Request):
    """
    Legacy endpoint from the old flow.
    You can keep it to avoid broken links, but it should no longer be used.
    """
    return RedirectResponse("/pmc/onboarding/pms", status_code=303)


@router.get("/pmc/signup/cancel", response_class=HTMLResponse)
def pmc_signup_cancel(request: Request):
    """
    Legacy endpoint from the old flow.
    """
    return RedirectResponse("/pmc/signup", status_code=303)
