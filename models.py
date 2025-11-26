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
    pms_secret = Column(String)
    active = Column(Boolean, default=True)
    sync_enabled = Column(Boolean, default=True)
    last_synced_at = Column(DateTime)

class Property(Base):
    __tablename__ = "properties"

    pms_property_id = Column(String, primary_key=True)  # 👈 make this the PK
    property_name = Column(String)
    pms_account_id = Column(Integer)
    pms_integration = Column(String)
    sandy_enabled = Column(Boolean)
    data_folder_path = Column(String)
    pmc_record_id = Column(String)
    last_synced = Column(DateTime)
