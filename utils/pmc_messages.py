from __future__ import annotations
from sqlalchemy.orm import Session
from datetime import datetime
from models import PMCMessage

def upsert_pmc_message(
    db: Session,
    *,
    pmc_id: int,
    dedupe_key: str | None,
    type: str,
    subject: str,
    body: str,
    severity: str = "info",
    status: str = "open",
    property_id: int | None = None,
    upgrade_purchase_id: int | None = None,
    upgrade_id: int | None = None,
    guest_session_id: int | None = None,
    link_url: str | None = None,
) -> PMCMessage:
    msg = None
    if dedupe_key:
        msg = db.query(PMCMessage).filter(PMCMessage.pmc_id == pmc_id, PMCMessage.dedupe_key == dedupe_key).first()

    if msg:
        msg.type = type
        msg.subject = subject
        msg.body = body
        msg.severity = severity
        msg.status = status
        msg.property_id = property_id
        msg.upgrade_purchase_id = upgrade_purchase_id
        msg.upgrade_id = upgrade_id
        msg.guest_session_id = guest_session_id
        msg.link_url = link_url
        msg.is_read = False
        db.add(msg)
        return msg

    msg = PMCMessage(
        pmc_id=pmc_id,
        dedupe_key=dedupe_key,
        type=type,
        subject=subject,
        body=body,
        severity=severity,
        status=status,
        property_id=property_id,
        upgrade_purchase_id=upgrade_purchase_id,
        upgrade_id=upgrade_id,
        guest_session_id=guest_session_id,
        link_url=link_url,
        is_read=False,
    )
    db.add(msg)
    return msg


def resolve_pmc_message(db: Session, *, pmc_id: int, dedupe_key: str) -> None:
    msg = db.query(PMCMessage).filter(PMCMessage.pmc_id == pmc_id, PMCMessage.dedupe_key == dedupe_key).first()
    if not msg:
        return
    msg.status = "resolved"
    db.add(msg)
