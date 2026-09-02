"""
db/database.py — SQLAlchemy connection setup.

NOTE: This is NOT related to user authentication or login sessions.
It is the Postgres connection/session factory used across the entire app.
Auth/RBAC arrives in Phase 4 as API middleware — it has nothing to do with this file.
"""
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/incident_investigator",
)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# All SQLAlchemy ORM models inherit from this Base.
# Base.metadata.create_all(engine) is called at app startup (main.py)
# and is the single source of truth for table schemas.
Base = declarative_base()


def get_db():
    """
    FastAPI dependency: yields a DB session, closes it after the request.
    Usage:
        @app.post("/something")
        def handler(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
