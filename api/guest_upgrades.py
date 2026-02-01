from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime

from app.services.upgrade_rules import Upgrade, StayContext, evaluate_upgrade


router = APIRouter(prefix="/guest", tags=["guest-upgrades"])


# -----------------------------
# Request/Response models
# -----------------------------

class EvaluatedUpgradeOut(BaseModel):
    id: int
    eligible: bool
    disabled_reason: str = ""
    opens_at: Optional[str] = None  # ISO
    price_cents: int
    title: str
    code: str


class EvaluatedUpgradesOut(BaseModel):
    property_id: int
    session_id: Optional[str] = None
    upgrades: List[EvaluatedUpgradeOut]


class CheckoutIn(BaseModel):
    session_id: Optional[str] = None


class CheckoutOut(BaseModel):
    checkout_url: str


# -----------------------------
# REPO ADAPTERS (YOU EDIT THESE)
# -----------------------------
# Keep all DB-specific logic here so rules stay clean.

async def repo_get_upgrades_for_property(property_id: int) -> List[Upgrade]:
    """
    Replace this with your DB call.
    Must return upgrades that include:
      id, property_id, code, title, price_cents, enabled
    """
    raise NotImplementedError("Connect your DB here.")


async def repo_get_stay_context(property_id: int, session_id: str) -> StayContext:
    """
    Replace this with your DB call.
    The key enterprise part: compute turnover flags based on reservations.

    Example logic you’ll likely implement:
      has_same_day_turnover_on_arrival =
         EXISTS reservation that DEPARTS on guest.arrival_date
      has_same_day_turnover_on_departure =
         EXISTS reservation that ARRIVES on guest.departure_date
    """
    raise NotImplementedError("Connect your DB here.")


async def repo_create_stripe_checkout(*, property_id: int, session_id: str, upgrade_id: int) -> str:
    """
    Replace with your existing Stripe checkout creation.
    Return checkout_url.
    """
    raise NotImplementedError("Wire your existing Stripe code here.")


# -----------------------------
# Helpers
# -----------------------------

def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


# -----------------------------
# Endpoints
# -----------------------------

@router.get("/properties/{property_id}/upgrades/evaluated", response_model=EvaluatedUpgradesOut)
async def get_evaluated_upgrades(property_id: int, session_id: str):
    upgrades = await repo_get_upgrades_for_property(property_id)
    if not upgrades:
        return EvaluatedUpgradesOut(property_id=property_id, session_id=session_id, upgrades=[])

    stay = await repo_get_stay_context(property_id, session_id)

    out: List[EvaluatedUpgradeOut] = []
    for up in upgrades:
        result = evaluate_upgrade(upgrade=up, stay=stay)
        out.append(
            EvaluatedUpgradeOut(
                id=up.id,
                eligible=result.eligible,
                disabled_reason=result.reason or "",
                opens_at=_iso(result.opens_at),
                price_cents=up.price_cents,
                title=up.title,
                code=up.code,
            )
        )

    return EvaluatedUpgradesOut(property_id=property_id, session_id=session_id, upgrades=out)


@router.post("/upgrades/{upgrade_id}/checkout", response_model=CheckoutOut)
async def start_checkout(upgrade_id: int, body: CheckoutIn):
    """
    IMPORTANT:
    - This endpoint must enforce eligibility (enterprise-grade)
    - Do NOT trust client-sent "disabled" state
    """
    session_id = (body.session_id or "").strip()
    if not session_id:
        raise HTTPException(status_code=401, detail="Missing session_id. Please unlock your stay first.")

    # You may also store property_id in the session on server-side.
    # If you must derive property_id from session_id, do it in repo_get_stay_context.
    # For now, we’ll require stay context to provide property_id.
    # If you already have property_id in your route, pass it. Otherwise derive it.

    # Here we derive property_id from stay context (recommended):
    #   stay = repo_get_stay_context_by_session(session_id) -> includes property_id
    # If your repo requires property_id, you can fetch upgrade first and use upgrade.property_id.
    # We'll do: fetch upgrade’s property_id by scanning property upgrades (simple placeholder).
    # Replace with direct DB lookup of upgrade by id.
    #
    # ---- START: lookup upgrade + property
    # Replace with repo_get_upgrade_by_id(upgrade_id)
    #
    # Minimal approach: find upgrade in any property you'd like:
    # BUT better: direct lookup.
    raise_if_missing = True

    # TODO: Replace this block with direct lookup
    # -----------------------------------------
    # Example:
    #   up = await repo_get_upgrade_by_id(upgrade_id)
    #   property_id = up.property_id
    #   stay = await repo_get_stay_context(property_id, session_id)
    # -----------------------------------------

    # Temporary NotImplemented until you wire your repo:
    raise NotImplementedError("Implement upgrade lookup + stay context lookup + then call repo_create_stripe_checkout.")


def register_guest_upgrades_routes(app):
    app.include_router(router)
