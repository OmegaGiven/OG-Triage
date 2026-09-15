"""Database engine/session setup, shared by seed.py, Alembic env.py, and (later) the API."""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

DEFAULT_DATABASE_URL = "postgresql+psycopg://og_triage:og_triage_dev_password@localhost:5544/og_triage"


def get_database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_engine():
    return create_engine(get_database_url(), future=True)


SessionLocal = sessionmaker(bind=get_engine(), future=True)
