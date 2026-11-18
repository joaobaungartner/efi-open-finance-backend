from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from config.settings import get_settings

settings = get_settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
kwargs = {}
if settings.database_url == "sqlite://":
    kwargs["poolclass"] = StaticPool

engine = create_engine(
    settings.database_url, pool_pre_ping=True, connect_args=connect_args, **kwargs
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
