
from sqlalchemy import (
    Column,
    BigInteger,
    Integer,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    Date,
    UniqueConstraint,
    func,
)


from sqlalchemy.orm import relationship
import json
from database import Base  # ✅ Use the shared Base from database.py
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from datetime import datetime, timezone


class AdminMessage(Base):
    __tablename__ = "admin_messages"

    id = sa.Column(sa.Integer, primary_key=True, index=True)

    # Scope
    pmc_id = sa.Column(sa.Integer, sa.ForeignKey("pmcs.id"), nullable=True, index=True)
    property_id = sa.Column(sa.Integer, sa.ForeignKey("properties.id"), nullable=True, index=True)

    # Type of message
    kind = sa.Column(sa.String(50), nullable=False, index=True)  # upgrade_purchase | upgrade_request | etc.

    # Content
    subject = sa.Column(sa.String(255), nullable=True)
    body = sa.Column(sa.Text, nullable=True)

    # Optional metadata
    meta = sa.Column(sa.JSON, nullable=True)

    # Read tracking
    read_at = sa.Column(sa.DateTime, nullable=True, index=True)

    # Timestamps
    created_at = sa.Column(sa.DateTime, nullable=False, default=datetime.utcnow, index=True)

    # optional relationships (if you want)
    # pmc = relationship("PMC")
    # property = relationship("Property")


# -------------------------------------------------------------------
# Integrations
# -------------------------------------------------------------------
class PMCIntegration(Base):
    __tablename__ = "pmc_integrations"

    id = Column(Integer, primary_key=True, index=True)
    pmc_id = Column(Integer, ForeignKey("pmc.id", ondelete="CASCADE"), nullable=False, index=True)

    # hostaway | lodgify | guesty | ownerrez | etc
    provider = Column(String, nullable=False, index=True)

    account_id = Column(String, nullable=True)
    api_key = Column(String, nullable=True)
    api_secret = Column(String, nullable=True)

    # for OAuth providers later
    access_token = Column(String, nullable=True)
    refresh_token = Column(String, nullable=True)
    token_expires_at = Column(DateTime, nullable=True)

    is_connected = Column(Boolean, default=False)

    last_synced_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    pmc = relationship("PMC", back_populates="integrations")
    properties = relationship("Property", back_populates="integration", cascade="all, delete-orphan")


    __table_args__ = (
        UniqueConstraint("pmc_id", "provider", name="uq_pmc_integrations_pmc_provider"),
    )

# -------------------------------------------------------------------
# PMSConnection model
# -------------------------------------------------------------------

class PMSConnection(Base):
    __tablename__ = "pms_connections"

    id = Column(Integer, primary_key=True, index=True)
    pmc_id = Column(Integer, ForeignKey("pmc.id", ondelete="CASCADE"), nullable=False, index=True)

    provider = Column(String, nullable=False, index=True)  # hostaway, lodgify, guesty, etc.
    status = Column(String, nullable=False, default="connected")  # connected, error, disconnected

    external_account_id = Column(String, nullable=True)  # account id / org id in provider
    auth_json = Column(Text, nullable=True)              # store creds/tokens (encrypt later)
    access_token = Column(String, nullable=True)
    refresh_token = Column(String, nullable=True)
    token_expires_at = Column(DateTime, nullable=True)

    last_sync_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    pmc = relationship("PMC", backref="pms_connections")

    __table_args__ = (
        UniqueConstraint("pmc_id", "provider", name="uq_pms_conn_pmc_provider"),
    )


# -------------------------------------------------------------------
# PMC USERS (staff / owner / admin)
# -------------------------------------------------------------------
class PMCUser(Base):
    __tablename__ = "pmc_users"

    id = Column(Integer, primary_key=True, index=True)
    pmc_id = Column(Integer, ForeignKey("pmc.id", ondelete="CASCADE"), nullable=False, index=True)

    email = Column(String, nullable=False, index=True)
    full_name = Column(String, nullable=True)

    role = Column(String, nullable=False, default="staff")

    is_active = Column(Boolean, default=True)
    is_superuser = Column(Boolean, nullable=False, default=False)

    # ✅ ADD THIS HERE
    notification_prefs = Column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb")
    )

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_login_at = Column(DateTime, nullable=True)

    pmc = relationship("PMC", back_populates="users")
    timezone = Column(String, nullable=True)  # e.g. "America/Los_Angeles"

    __table_args__ = (
        UniqueConstraint("pmc_id", "email", name="uq_pmc_users_pmc_email"),
    )



# -------------------------------------------------------------------
# RESERVATIONS
# -------------------------------------------------------------------
class Reservation(Base):
    __tablename__ = "reservations"

    id = Column(Integer, primary_key=True, index=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False, index=True)

    # PMS reference
    pms_reservation_id = Column(String, unique=True, index=True)

    # Guest info
    guest_name = Column(String, nullable=True)
    phone_last4 = Column(String(4), nullable=True)

    # Dates
    arrival_date = Column(Date, nullable=False)
    departure_date = Column(Date, nullable=False)

    # Times (PMS strings like "15:00")
    checkin_time = Column(String, nullable=True)
    checkout_time = Column(String, nullable=True)

    property = relationship("Property", back_populates="reservations")


# -------------------------------------------------------------------
# GUIDES
# -------------------------------------------------------------------
class Guide(Base):
    __tablename__ = "guides"

    id = Column(Integer, primary_key=True, index=True)
    property_id = Column(Integer, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True)

    title = Column(String, nullable=False)
    image_url = Column(String, nullable=True)
    short_description = Column(String, nullable=True)
    long_description = Column(Text, nullable=True)
    body_html = Column(Text, nullable=True)

    category = Column(String, nullable=True)

    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    property = relationship("Property", back_populates="guides")


# -------------------------------------------------------------------
# PMC (Property Management Company)
# -------------------------------------------------------------------
class PMC(Base):
    __tablename__ = "pmc"

    id = Column(Integer, primary_key=True, index=True)

    pmc_name = Column(String, nullable=False)
    email = Column(String, nullable=False, index=True)  # owner/admin email
    main_contact = Column(String, nullable=True)

    subscription_plan = Column(String, nullable=True)

    pms_integration = Column(String, nullable=True)
    pms_api_key = Column(String, nullable=True)
    pms_account_id = Column(String, nullable=True)
    pms_api_secret = Column(String, nullable=True)

    active = Column(Boolean, default=True)
    sync_enabled = Column(Boolean, default=True)
    last_synced_at = Column(DateTime, nullable=True)

    # ✅ BILLING (required for your Stripe flow)
    billing_status = Column(String, default="pending")  # pending | active | past_due | canceled
    stripe_customer_id = Column(String, nullable=True)
    stripe_signup_checkout_session_id = Column(String, nullable=True)
    signup_paid_at = Column(DateTime, nullable=True)

    # later: when you add recurring subscriptions based on enabled properties
    stripe_subscription_id = Column(String, nullable=True)
    stripe_subscription_item_id = Column(String, nullable=True)

    properties = relationship("Property", back_populates="pmc", cascade="all, delete-orphan")
    users = relationship("PMCUser", back_populates="pmc", cascade="all, delete-orphan")
    integrations = relationship("PMCIntegration", back_populates="pmc", cascade="all, delete-orphan")


# -------------------------------------------------------------------
# PROPERTIES
# -------------------------------------------------------------------
class Property(Base):
    __tablename__ = "properties"

    id = Column(Integer, primary_key=True, index=True)

    pmc_id = Column(
        Integer,
        ForeignKey("pmc.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ✅ NEW: tie each property row to the exact pmc_integrations row
    integration_id = Column(
        Integer,
        ForeignKey("pmc_integrations.id", ondelete="CASCADE"),
        nullable=True,   # keep nullable during transition; make False later
        index=True,
    )

    provider = Column(String, nullable=True, index=True)

    # PMS-native ID (Hostaway listing id)
    pms_property_id = Column(String, nullable=True, index=True)

    # ✅ NEW: normalized external id (for uniform lookups; same as pms_property_id for Hostaway)
    external_property_id = Column(String, nullable=True, index=True)

    property_name = Column(String, nullable=False)
    sandy_enabled = Column(Boolean, default=False, nullable=False)

    data_folder_path = Column(String, nullable=True)
    last_synced = Column(DateTime, nullable=True)

    hero_image_url = Column(String, nullable=True)

    # Relationships
    pmc = relationship("PMC", back_populates="properties")

    integration = relationship("PMCIntegration", back_populates="properties")

    reservations = relationship("Reservation", back_populates="property", cascade="all, delete-orphan")
    guides = relationship("Guide", back_populates="property", cascade="all, delete-orphan")
    upgrades = relationship("Upgrade", back_populates="property", cascade="all, delete-orphan")
    chat_sessions = relationship("ChatSession", back_populates="property", cascade="all, delete-orphan")

    chat_enabled = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        # ✅ New correct uniqueness rule (what your sync uses)
        UniqueConstraint("integration_id", "external_property_id", name="uq_properties_integration_external"),

        # Optional: keep legacy uniqueness during transition (safe)
        UniqueConstraint("pmc_id", "provider", "pms_property_id", name="uq_properties_provider_pms_id"),
    )



# -------------------------------------------------------------------
# MESSAGES
# -------------------------------------------------------------------

def utcnow():
    return datetime.now(timezone.utc)

class PMCMessage(Base):
    __tablename__ = "pmc_messages"

    id = Column(Integer, primary_key=True, index=True)
    pmc_id = Column(Integer, ForeignKey("pmc.id"), nullable=False, index=True)

    # Useful fields for admin UI
    type = Column(String(50), nullable=False, default="upgrade_request")  # e.g. upgrade_request
    subject = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)

    # Optional metadata you may want later
    property_id = Column(Integer, nullable=True, index=True)
    upgrade_purchase_id = Column(Integer, nullable=True, index=True)
    upgrade_id = Column(Integer, nullable=True, index=True)
    guest_session_id = Column(Integer, nullable=True, index=True)

    is_read = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    pmc = relationship("PMC")


# -------------------------------------------------------------------
# UPGRADES
# -------------------------------------------------------------------
class Upgrade(Base):
    __tablename__ = "upgrades"

    id = Column(Integer, primary_key=True, index=True)
    property_id = Column(Integer, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True)

    # e.g. "early-check-in"
    slug = Column(String, nullable=False)

    title = Column(String, nullable=False)
    short_description = Column(String, nullable=True)

    price_cents = Column(Integer, nullable=False, default=0)
    currency = Column(String, nullable=False, default="usd")

    image_url = Column(String, nullable=True)  # ✅ add this
    long_description = Column(Text, nullable=True)  # ✅ since you asked

    stripe_price_id = Column(String, nullable=True)

    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    property = relationship("Property", back_populates="upgrades")

    # ✅ slug should be unique PER PROPERTY (not globally)
    __table_args__ = (
        UniqueConstraint("property_id", "slug", name="uq_upgrades_property_slug"),
    )


# -------------------------------------------------------------------
# CHAT SESSIONS
# -------------------------------------------------------------------
class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True, index=True)
    property_id = Column(Integer, ForeignKey("properties.id", ondelete="CASCADE"), index=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_activity_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    source = Column(String, default="guest_web")  # guest_web, widget, admin_test
    reservation_status = Column(String, default="pre_booking")

    # --- Admin routing / triage ---
    # urgent | high | normal | low
    action_priority = Column(String, nullable=True, index=True)

    # --- Guest mood (UI expects these) ---
    # primary mood label (optional)
    guest_mood = Column(String, nullable=True)
    # 0-100 confidence (optional)
    guest_mood_confidence = Column(Integer, nullable=True)

    # emotional_signals: array of mood strings (source of truth for UI)
    emotional_signals = Column(
        JSONB,
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    )

    # signals: any non-mood structured flags you may track later
    signals = Column(
        JSONB,
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    )

    is_verified = Column(Boolean, default=False)

    phone_last4 = Column(String, nullable=True)
    pms_reservation_id = Column(String, nullable=True)
    language = Column(String, nullable=True)

    guest_name = Column(String, nullable=True)
    arrival_date = Column(String, nullable=True)    # store as "YYYY-MM-DD"
    departure_date = Column(String, nullable=True)

    ai_summary = Column(Text, nullable=True)
    ai_summary_updated_at = Column(DateTime, nullable=True)

    is_resolved = Column(Boolean, default=False)
    resolved_at = Column(DateTime, nullable=True)

    escalation_level = Column(String, nullable=True)  # low/medium/high
    assigned_to = Column(String, nullable=True)
    internal_note = Column(Text, nullable=True)
    updated_at = Column(DateTime, nullable=True)
    heat_score = Column(Integer, default=0)

    __table_args__ = (
        # fast list sorting + paging per property
        sa.Index("ix_chat_sessions_property_last_activity", "property_id", "last_activity_at"),

        # fast list filtering
        sa.Index("ix_chat_sessions_action_priority", "action_priority"),
        sa.Index("ix_chat_sessions_escalation_level", "escalation_level"),

        # ✅ fast JSONB containment queries:
        # WHERE emotional_signals @> '["worried"]'::jsonb
        sa.Index(
            "ix_chat_sessions_emotional_signals_gin",
            "emotional_signals",
            postgresql_using="gin",
        ),
    )

    property = relationship("Property", back_populates="chat_sessions")
    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")


# -------------------------------------------------------------------
# CHAT MESSAGES
# -------------------------------------------------------------------
class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True, nullable=False)

    sender = Column(String, nullable=False)  # guest | assistant | system
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Intelligence fields
    category = Column(String, nullable=True)
    log_type = Column(String, nullable=True)
    sentiment = Column(String, nullable=True)

     # ✅ NEW: rich structured sentiment payload
    sentiment_data = Column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb")
    )

    session = relationship("ChatSession", back_populates="messages")

# -------------------------------------------------------------------
# ANALYTICS EVENTS (append-only)
# -------------------------------------------------------------------
class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    id = Column(Integer, primary_key=True, index=True)

    # --- Ownership / scope ---
    pmc_id = Column(
        Integer,
        ForeignKey("pmc.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    property_id = Column(
        Integer,
        ForeignKey("properties.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # --- Chat linkage ---
    thread_id = Column(String, nullable=True, index=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=True, index=True)

    message_id = Column(String, nullable=True, index=True)
    parent_id = Column(String, nullable=True, index=True)

    # --- Event metadata ---
    event_name = Column(String, nullable=False, index=True)
    sender = Column(String, nullable=True)       # user | bot | system
    variant = Column(String, nullable=True)      # normal | system | error
    length = Column(Integer, nullable=True)

    # flexible payload (NO MIGRATIONS needed later)
    data = Column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # --- Relationships (optional, but useful later) ---
    pmc = relationship("PMC")
    property = relationship("Property")
    session = relationship("ChatSession")


# -------------------------------------------------------------------
# UPGRADES BASE
# -------------------------------------------------------------------


class UpgradePurchase(Base):
    __tablename__ = "upgrade_purchases"

    id = Column(Integer, primary_key=True)

    pmc_id = Column(Integer, ForeignKey("pmc.id", ondelete="CASCADE"), nullable=False, index=True)
    property_id = Column(Integer, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True)
    upgrade_id = Column(Integer, ForeignKey("upgrades.id", ondelete="CASCADE"), nullable=False, index=True)

    # ✅ ADD THIS
    guest_session_id = Column(
        Integer,
        ForeignKey("chat_sessions.id"),
        nullable=True,
        index=True,
    )


    amount_cents = Column(Integer, nullable=False)
    platform_fee_cents = Column(Integer, nullable=False, default=0)
    net_amount_cents = Column(Integer, nullable=False)

    currency = Column(String, nullable=False, default="usd")

    # pending | paid | refunded | canceled | failed
    status = Column(String, nullable=False, default="pending", index=True)

    # Stripe tracking
    stripe_checkout_session_id = Column(String, unique=True, index=True, nullable=True)
    stripe_payment_intent_id = Column(String, unique=True, index=True, nullable=True)
    stripe_transfer_id = Column(String, unique=True, index=True, nullable=True)
    stripe_destination_account_id = Column(String, nullable=True, index=True)

    paid_at = Column(DateTime(timezone=True), nullable=True)
    refunded_at = Column(DateTime(timezone=True), nullable=True)
    refunded_amount_cents = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Optional relationships (only add if you want them)
    property = relationship("Property")
    upgrade = relationship("Upgrade")
    session = relationship("ChatSession", foreign_keys=[guest_session_id])

    __table_args__ = (
        # ✅ Prevent duplicate PAID purchases per stay+upgrade (works well with your "status == paid" check)
        # Note: if guest_session_id can be NULL, this constraint won't protect NULL rows (Postgres behavior).
        # If you want stronger protection, make guest_session_id non-null for guest flows.
        UniqueConstraint("guest_session_id", "upgrade_id", name="uq_upgrade_purchase_guest_session_upgrade"),
    )
