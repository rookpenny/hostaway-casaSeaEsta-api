from models import Base
from database import engine

print("Creating tables...")
Base.metadata.create_all(bind=engine)
print("✅ Database schema created.")
