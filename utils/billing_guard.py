# utils/billing_guard.py
from fastapi import HTTPException
from sqlalchemy.orm import Session
from models import PMC

def require_pmc_is_paid(db: Session, pmc_id: int):
    pmc = db.query(PMC).filter(PMC.id == pmc_id).first()
    if not pmc:
        raise HTTPException(status_code=404, detail="PMC not found")

    if (pmc.billing_status or "pending") != "active":
        # 402 is semantically correct for paywall
        raise HTTPException(status_code=402, detail="Payment required")

    if not pmc.active:
        raise HTTPException(status_code=403, detail="PMC inactive")

    return pmc
