from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime

from database import Base  # ✅ Use the shared Base from database.py


class Reservation(Base):
    __tablename__ = "reservations"

    id = Column(Integer, primary_key=True, index=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False)

    # PMS reference
    pms_reservation_id = Column(String, unique=True, index=True)

    # Guest info
    guest_name = Column(String)
    phone_last4 = Column(String(4))

    # Dates
    arrival_date = Column(Date, nullable=False)
    departure_date = Column(Date, nullable=False)

    # Times – you can use Time or String depending on how your PMS gives it to you
    checkin_time = Column(String, nullable=True)   # e.g. "15:00"
    checkout_time = Column(String, nullable=True)  # e.g. "11:00"

    # Relationship back to Property
    property = relationship("Property", back_populates="reservations")


class PMC(Base):
    __tablename__ = "pmc"

    id = Column(Integer, primary_key=True, index=True)
    pmc_name = Column(String)
    email = Column(String)
    main_contact = Column(String)
    subscription_plan = Column(String)
    pms_integration = Column(String)
    pms_api_key = Column(String)
    pms_account_id = Column(String)
    pms_api_secret = Column(String)
    active = Column(Boolean, default=True)
    sync_enabled = Column(Boolean, default=True)
    last_synced_at = Column(DateTime)

    # ✅ One-to-many relationship
    properties = relationship("Property", back_populates="pmc", cascade="all, delete-orphan")


class Property(Base):
    __tablename__ = "properties"

    id = Column(Integer, primary_key=True)
    property_name = Column(String)
    pms_property_id = Column(String, unique=True, index=True)
    pms_integration = Column(String)
    pmc_id = Column(Integer, ForeignKey("pmc.id"))
    sandy_enabled = Column(Boolean, default=True)
    data_folder_path = Column(String)
    last_synced = Column(DateTime)

    # ✅ Back-reference to PMC
    pmc = relationship("PMC", back_populates="properties")
    reservations = relationship("Reservation", back_populates="property") o


class Upgrade(Base):
    __tablename__ = "upgrades"

    id = Column(Integer, primary_key=True, index=True)

    # which property this upgrade belongs to
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False, index=True)

    # e.g. "early-check-in", "mid-stay-clean"
    slug = Column(String, unique=True, index=True)

    # short label (card title)
    title = Column(String, nullable=False)

    # short blurb under the title (card)
    short_description = Column(String, nullable=True)

    # full description on the detail page
    long_description = Column(Text, nullable=True)

    # pricing
    price_cents = Column(Integer, nullable=False, default=0)  # e.g. 7500 = $75.00
    currency = Column(String, nullable=False, default="usd")

    # Stripe linkage (Price ID from Stripe dashboard)
    stripe_price_id = Column(String, nullable=True)

    # display / ordering
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    # relationship back to Property
    property = relationship("Property", backref="upgrades")



class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True, index=True)
    property_id = Column(Integer, ForeignKey("properties.id"), index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_activity_at = Column(DateTime, default=datetime.utcnow)
    source = Column(String, default="guest_web")  # e.g. guest_web, widget, admin_test

    # for future guest verification
    is_verified = Column(Boolean, default=False)
    phone_last4 = Column(String, nullable=True)
    pms_reservation_id = Column(String, nullable=True)
    language = Column(String, nullable=True)

    # 🔹 NEW FIELDS
    guest_name = Column(String, nullable=True)
    arrival_date = Column(String, nullable=True)     # store as "YYYY-MM-DD" from Hostaway
    departure_date = Column(String, nullable=True)

    property = relationship("Property", backref="chat_sessions")
    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), index=True, nullable=False)
    sender = Column(String, nullable=False)  # 'guest' | 'assistant' | 'system'
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Intelligence fields
    category = Column(String, nullable=True)   # urgent, maintenance, cleaning, etc.
    log_type = Column(String, nullable=True)   # Prearrival Interest, Extension, General, etc.
    sentiment = Column(String, nullable=True)  # positive, neutral, negative

    session = relationship("ChatSession", back_populates="messages")
