from fastapi import FastAPI

from db.database import Base, engine
from routers import incidents, query

# Create all tables defined in models/db_models.py if they don't exist.
# In production you'd use Alembic migrations; create_all() is appropriate
# for solo/portfolio development.
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Incident Investigator",
    description=(
        "Semantic search + reasoning over an org's incident history. "
        "Ingests postmortems and synthetic logs, answers incident descriptions "
        "with grounded root-cause analysis and citations."
    ),
    version="0.1.0",
)

app.include_router(incidents.router)
app.include_router(query.router)


@app.get("/health")
def health():
    """Liveness check — confirm the app is running and DB tables exist."""
    return {"status": "ok"}
