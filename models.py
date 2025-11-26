from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
from database import Base

Base = declarative_base()

class PMC(Base):
    __tablename__ = "pmc"

    pmc_name = Column(String, nullable=False)
    email = Column(String, nullable=True)
    main_contact = Column(String, nullable=True)
    subscription_plan = Column(String, nullable=True)
    pms_integration = Column(String, nullable=True)
    pms_client_id = Column(String, primary_key=True)  # ← stays as PK for now
    pms_secret = Column(String, nullable=True)
    pms_account_id = Column(Integer, unique=True, index=True)
    active = Column(Boolean, default=False)
    sync_enabled = Column(Boolean, default=False)
    last_synced_at = Column(DateTime, nullable=True)  # ✅ new column




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
