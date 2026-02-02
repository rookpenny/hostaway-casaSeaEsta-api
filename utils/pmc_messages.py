from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from models import PMCMessage


def upsert_pmc_message(
    db: Session,
    *,
    pmc_id: int,
    dedupe_key: Optional[str],
    type: str,
    subject: str,
    body: str,
    severity: str = "info",
    status: str = "open",
    property_id: Optional[int] = None,
    upgrade_purchase_id: Optional[int] = None,
    upgrade_id: Optional[int] = None,
    guest_session_id: Optional[int] = None,
    link_url: Optional[str] = None,
) -> PMCMessage:
    """
    Create or update a PMCMessage row.

    - If dedupe_key is provided, upserts on (pmc_id, dedupe_key).
    - Always marks message as unread when updated/created.
    - Does NOT commit; caller controls transaction boundaries.
    """
    msg: Optional[PMCMessage] = None

    if dedupe_key:
        msg = (
            db.query(PMCMessage)
            .filter(PMCMessage.pmc_id == int(pmc_id), PMCMessage.dedupe_key == dedupe_key)
            .first()
        )

    if msg is None:
        msg = PMCMessage(pmc_id=int(pmc_id), dedupe_key=dedupe_key)

    msg.type = type
    msg.subject = subject
    msg.body = body

    # Backward-compatible: only set if model has fields
    if hasattr(msg, "severity"):
        msg.severity = severity
    if hasattr(msg, "status"):
        msg.status = status

    msg.property_id = property_id
    msg.upgrade_purchase_id = upgrade_purchase_id
    msg.upgrade_id = upgrade_id
    msg.guest_session_id = guest_session_id

    if hasattr(msg, "link_url"):
        msg.link_url = link_url

    msg.is_read = False

    db.add(msg)
    return msg


def resolve_pmc_message(db: Session, *, pmc_id: int, dedupe_key: str) -> None:
    """
    Mark an existing message as resolved (and leave it unread=False as-is).
    Does NOT commit.
    """
    if not dedupe_key:
        return

    msg = (
        db.query(PMCMessage)
        .filter(PMCMessage.pmc_id == int(pmc_id), PMCMessage.dedupe_key == dedupe_key)
        .first()
    )
    if not msg:
        return

    if hasattr(msg, "status"):
        msg.status = "resolved"

    db.add(msg)
