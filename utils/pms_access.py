# utils/pms_access.py

from __future__ import annotations
from typing import Tuple, Optional

from models import PMC, Property, ChatSession
from utils.hostaway import get_upcoming_phone_for_listing
from sqlalchemy.orm import Session

def get_pms_access_info(
    pmc: PMC,
    prop: Property,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Resolve guest phone_last4, door_code, and pms_reservation_id for a given property.

    Returns:
        (phone_last4, door_code, pms_reservation_id)
        or (None, None, None) if we can't resolve anything.
    """

    integration = (prop.pms_integration or pmc.pms_integration or "").lower()

    # No PMS integration configured
    if not integration:
        print("[PMS] No PMS integration configured for PMC/property")
        return None, None, None


    from sqlalchemy.orm import Session
from models import Property, ChatSession


def ensure_pms_data(db: Session, chat_session: ChatSession) -> None:
    """
    Make sure this chat session has PMS info attached (phone_last4 + reservation_id)
    for the property's current/upcoming guest.
    """
    # Get the property for this chat session
    prop = db.query(Property).filter(Property.id == chat_session.property_id).first()
    if not prop:
        print(f"[PMS] No property found for chat_session.id={chat_session.id}")
        return

    pmc = prop.pmc
    if not pmc:
        print(f"[PMS] No PMC found for property.id={prop.id}")
        return

    phone_last4, door_code, reservation_id = get_pms_access_info(pmc, prop)

    if not reservation_id:
        # No active/upcoming reservation – nothing to attach
        return

    # Persist PMS info on the chat session
    chat_session.phone_last4 = phone_last4
    chat_session.pms_reservation_id = reservation_id
    db.add(chat_session)
    db.commit()

    # 🔐 4a) Asking for door code but not verified → start verification flow
    if is_code_request and not session.is_verified:
        ensure_pms_data(db, chat_session)

        if not phone_last4:
            return {
                "response": (
                    "I’m not seeing an active reservation with a phone number for this property right now. 🤔\n\n"
                    "Please double-check that you’re using the correct link, or contact your host directly "
                    "so they can share your code."
                ),
                "session_id": session.id,
            }

        return {
            "response": (
                "For security, I can only share your access code after I confirm your identity. 🔐\n\n"
                "What are the **last 4 digits of the phone number** on your reservation? 📱"
            ),
            "session_id": session.id,
        }

    # 🔐 4b) 4-digit message & not verified → treat as verification attempt
    four_digits = re.fullmatch(r"\d{4}", user_message)
    if four_digits and not session.is_verified:
        ensure_pms_data()

        if not phone_last4:
            return {
                "response": (
                    "I’m not seeing a phone number on file for your reservation, so I can’t verify you automatically. 😕\n\n"
                    "Please contact your host directly and they’ll share your code."
                ),
                "session_id": session.id,
            }

        if phone_last4 == user_message:
            session.is_verified = True
            session.phone_last4 = user_message
            session.last_activity_at = now
            db.commit()

            return {
                "response": (
                    "Thank you! You're verified 🎉\n\n"
                    "You can now ask me for your **door code** or anything else you need."
                ),
                "session_id": session.id,
            }
        else:
            return {
                "response": (
                    "Hmm, that doesn’t match the phone number I have on file. 😕\n\n"
                    "Please double-check the **last 4 digits** of the phone number on your reservation, "
                    "or contact your host directly if you think there’s an issue."
                ),
                "session_id": session.id,
            }

    # 🔐 4c) Verified & asking for code → time-gated reveal
    if is_code_request and session.is_verified:
        ensure_pms_data()

        if not door_code:
            return {
                "response": (
                    "You're verified 🎉 but I don’t see a door code configured for this reservation in the PMS.\n\n"
                    "Please contact your host directly so they can share it with you."
                ),
                "session_id": session.id,
            }

        now_hour = now.hour  # TODO: adjust for property timezone later

        if now_hour < checkin_hour:
            return {
                "response": (
                    "You're verified 🎉 but it's a bit early for check-in.\n\n"
                    f"Your standard check-in time is **{checkin_time}**.\n\n"
                    "🌟 If you'd like, I can check on **early check-in** options for you."
                ),
                "session_id": session.id,
            }

        return {
            "response": (
                "You're all set! 🎉\n\n"
                "Your access code (based on the phone number on your reservation) is:\n\n"
                f"➡️ **{door_code}** 🔓"
            ),
            "session_id": session.id,
        }

    # 5️⃣ Everything else → normal Sandy GPT reply
    system_prompt = (
        "You are Sandy, a beachy, upbeat AI concierge for a vacation rental.\n"
        "Always respond in the same language as the guest.\n"
        "Use markdown with:\n"
        "- Bold section headings\n"
        "- Bullet points\n"
        "- Short paragraphs\n"
        "- Emojis where helpful\n\n"
        f"This message is for property: {prop.property_name} (ID: {prop.id})."
    )

    try:
        ai_response = client.chat.completions.create(
            model="gpt-4",
            temperature=0.8,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        reply_text = ai_response.choices[0].message.content.strip()
    except Exception as e:
        logging.exception("Error calling OpenAI in property_chat: %s", e)
        reply_text = (
            "Oops, I had a little trouble thinking just now 🧠💭\n\n"
            "Please try again in a moment, or contact your host directly if it's urgent."
        )

    assistant_msg = ChatMessage(
        session_id=session.id,
        sender="assistant",
        content=reply_text,
        created_at=datetime.utcnow(),
    )
    db.add(assistant_msg)
    session.last_activity_at = datetime.utcnow()
    db.commit()

    return {
        "response": reply_text,
        "session_id": session.id,
    }
