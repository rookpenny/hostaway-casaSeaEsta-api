from sqlalchemy import Column, Integer, String, Boolean
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class PMC(Base):
    __tablename__ = "pmc"

    id = Column(Integer, primary_key=True, index=True)
    pmc_name = Column(String, nullable=False)
    email = Column(String, nullable=True)
    main_contact = Column(String, nullable=True)
    subscription_plan = Column(String, nullable=True)
    pms_integration = Column(String, nullable=True)
    pms_client_id = Column(String, nullable=True)
    pms_secret = Column(String, nullable=True)
    pms_account_id = Column(Integer, unique=True, index=True)
    active = Column(Boolean, default=False)
    sync_enabled = Column(Boolean, default=False)
