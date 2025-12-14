# services/pms_sync.py
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy.orm import Session
from models import PMC

def sync_properties_for_pmc(db: Session, pmc_id: int) -> tuple[int, str | None]:
    pmc = db.query(PMC).filter(PMC.id == pmc_id).first()
    if not pmc:
        raise HTTPException(status_code=404, detail="PMC not found")

    if not pmc.pms_integration or not pmc.pms_account_id:
        raise HTTPException(status_code=400, detail="PMC is not connected to a PMS")

    # TODO: call provider-specific sync here (Hostaway first, Lodgify later)
    # count = sync_hostaway_properties(db, pmc)
    count = 0  # placeholder until wired

    pmc.last_synced_at = datetime.utcnow()
    db.commit()

    synced_at = pmc.last_synced_at.isoformat() if pmc.last_synced_at else None
    return count, synced_at
