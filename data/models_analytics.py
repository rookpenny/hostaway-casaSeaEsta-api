from sqlalchemy import Column, BigInteger, Text, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from database import Base

class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    id = Column(BigInteger, primary_key=True, index=True)
    ts = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    pmc_id = Column(BigInteger, nullable=True)
    property_id = Column(BigInteger, nullable=True)
    session_id = Column(BigInteger, nullable=True)
    user_id = Column(BigInteger, nullable=True)

    event_name = Column(Text, nullable=False)
    context = Column(JSONB, nullable=False, server_default="{}")
    data = Column(JSONB, nullable=False, server_default="{}")
