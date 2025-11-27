from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
from database import Base

Base = declarative_base()

class PMC(Base):
    __tablename__ = "pmc"

    id = Column(Integer, primary_key=True, index=True)
    pmc_name = Column(String)
    email = Column(String)
    main_contact = Column(String)
    subscription_plan = Column(String)
    pms_integration = Column(String)
    pms_api_key = Column(String)  # ✅ instead of pms_client_id
    pms_account_id = Column(String)
    pms_api_secret = Column(String)
    active = Column(Boolean, default=True)
    sync_enabled = Column(Boolean, default=True)
    last_synced_at = Column(DateTime)

class Property(Base):
    __tablename__ = "properties"

    id = Column(Integer, primary_key=True)
    name = Column(String)
    pms_property_id = Column(String, unique=True, index=True)
    pms_integration = Column(String)
    pmc_id = Column(Integer, ForeignKey("pmc.id"))
    sandy_enabled = Column(Boolean, default=True)
    data_folder_path = Column(String)
    last_synced = Column(DateTime)

