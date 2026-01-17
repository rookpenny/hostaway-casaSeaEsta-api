# database.py
import os
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from sqlalchemy.engine.url import make_url

# -------------------------------------------------------------------
# DATABASE URL
# -------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")

# Render / Heroku often provide postgres:// — SQLAlchemy needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# 🔒 Force psycopg2 explicitly (you have psycopg2-binary installed)
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgresql://",
        "postgresql+psycopg2://",
        1
    )

url = make_url(DATABASE_URL)

# -------------------------------------------------------------------
# ENGINE CONFIG
# -------------------------------------------------------------------
# Render / managed Postgres can drop idle connections.
# These settings reduce stale connections + make reconnects smoother.
engine_kwargs = {
    "future": True,
    "pool_pre_ping": True,   # checks connection liveness before using it
    "pool_recycle": int(os.getenv("DB_POOL_RECYCLE_SECONDS", "300")),  # default 5 min
    "pool_size": int(os.getenv("DB_POOL_SIZE", "5")),
    "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "10")),
    "pool_timeout": int(os.getenv("DB_POOL_TIMEOUT", "30")),
}

# Enable SQL logging only if explicitly requested
if os.getenv("SQL_ECHO", "").lower() in {"1", "true", "yes"}:
    engine_kwargs["echo"] = True

# SSL handling (Render / managed Postgres)
query = dict(url.query)
sslmode = (query.get("sslmode") or "").lower()

if sslmode == "require" or os.getenv("DB_SSL_REQUIRE", "").lower() in {"1", "true", "yes"}:
    engine_kwargs["connect_args"] = {"sslmode": "require"}

engine = create_engine(url, **engine_kwargs)

# -------------------------------------------------------------------
# SESSION + BASE
# -------------------------------------------------------------------
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,  # important for Jinja templates
)

Base = declarative_base()

# -------------------------------------------------------------------
# DEPENDENCY
# -------------------------------------------------------------------
def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
