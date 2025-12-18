from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    Date,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
import json
from database import Base  # ✅ Use the shared Base from database.py


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

    # owner | admin | staff | cleaner | maintenance | ops_manager | read_only
    role = Column(String, nullable=False, default="staff")

    is_active = Column(Boolean, default=True)

    # ✅ add this
    is_superuser = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_login_at = Column(DateTime, nullable=True)

    pmc = relationship("PMC", back_populates="users")

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
    short_description = Column(String, nullable=True)
    long_description = Column(Text, nullable=True)
    body_html = Column(Text, nullable=True)

    category = Column(String, nullable=True)
    image_url = Column(String, nullable=True)

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

    # Relationships
    pmc = relationship("PMC", back_populates="properties")

    integration = relationship("PMCIntegration", back_populates="properties")

    reservations = relationship("Reservation", back_populates="property", cascade="all, delete-orphan")
    guides = relationship("Guide", back_populates="property", cascade="all, delete-orphan")
    upgrades = relationship("Upgrade", back_populates="property", cascade="all, delete-orphan")
    chat_sessions = relationship("ChatSession", back_populates="property", cascade="all, delete-orphan")

    __table_args__ = (
        # ✅ New correct uniqueness rule (what your sync uses)
        UniqueConstraint("integration_id", "external_property_id", name="uq_properties_integration_external"),

        # Optional: keep legacy uniqueness during transition (safe)
        UniqueConstraint("pmc_id", "provider", "pms_property_id", name="uq_properties_provider_pms_id"),
    )


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
    long_description = Column(Text, nullable=True)

    price_cents = Column(Integer, nullable=False, default=0)
    currency = Column(String, nullable=False, default="usd")

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

    session = relationship("ChatSession", back_populates="messages")
