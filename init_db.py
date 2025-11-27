from database import engine
from models import Base

# This will create all tables based on your updated models.py
Base.metadata.create_all(bind=engine)

print("✅ Database schema created.")
