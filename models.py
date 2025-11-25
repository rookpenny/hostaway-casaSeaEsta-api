from sqlalchemy import Column, String, Boolean
from database import Base

class PMC(Base):
    __tablename__ = "pmc"

    id = Column(String, primary_key=True, index=True)
    pmc_name = Column(String)
    hostaway_account_id = Column(String)
    main_contact = Column(String)
    email = Column(String)
    subscription_plan = Column(String)
    pms_integration = Column(String)
    active = Column(Boolean, default=True)
    pms_account_id = Column(String)
