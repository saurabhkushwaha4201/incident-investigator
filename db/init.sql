-- =============================================================================
-- init.sql — runs once when the Postgres container first starts.
--
-- IMPORTANT: This file does NOT define table schemas.
--            Tables are created by SQLAlchemy Base.metadata.create_all()
--            at app startup (db/database.py). Keeping schema in one place
--            (Python models) prevents drift between SQL DDL and ORM definitions.
--
-- This file only:
--   1. Enables the pgvector extension (must happen before create_all runs)
--   2. Creates the test database (pytest uses a separate DB, never the dev DB)
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- Test database for pytest. The dev DB (incident_investigator) is created
-- by the POSTGRES_DB env var in docker-compose.yml — do not create it here
-- or Postgres will error on "database already exists".
CREATE DATABASE incident_investigator_test;

-- Enable vector in the test DB too
\c incident_investigator_test
CREATE EXTENSION IF NOT EXISTS vector;
\c incident_investigator
