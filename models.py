from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime

from database import Base  # ✅ Use the shared Base from database.py

#Base = declarative_base()

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
