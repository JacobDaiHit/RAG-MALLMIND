import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

if APP_ENV == "production" and not DATABASE_URL:
    raise RuntimeError("DATABASE_URL must be set explicitly when APP_ENV=production")

if not DATABASE_URL:
    DATABASE_URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/langchain_app"

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
Base = declarative_base()


def init_db() -> None:
    # Delayed import to avoid circular dependency.
    from rag.schemas import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
