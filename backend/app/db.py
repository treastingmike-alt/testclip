"""Database engine and session handling.

DATABASE_URL drives everything, so the same code runs on SQLite locally and
Postgres in production:

    sqlite:///./clipper.db                          (default, no setup needed)
    postgresql+psycopg2://user:pass@host/clipper    (production)

SQLite is the default deliberately -- requiring a Postgres install before the
app will boot is a bad first-run experience, and every model here uses portable
column types (JSON, String, DateTime) so the switch is a config change only.
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DEFAULT_SQLITE = "sqlite:///" + os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "clipper.db"
)
DATABASE_URL = os.environ.get("DATABASE_URL", DEFAULT_SQLITE)

# check_same_thread is a SQLite-only concern: FastAPI runs background tasks on a
# different thread than the request that created them, and the pipeline updates
# job rows from there.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_session():
    """FastAPI dependency: one session per request, always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    """Creates any missing tables. Safe to call on every startup."""
    from app import models  # noqa: F401  -- registers the mappers
    Base.metadata.create_all(bind=engine)
