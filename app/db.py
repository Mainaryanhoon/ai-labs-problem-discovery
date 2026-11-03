from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    url = get_settings().database_url
    if url.startswith("sqlite:///"):
        db_path = url.replace("sqlite:///", "", 1)
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def initialize_database(target_engine=None) -> None:
    """Create missing tables without altering existing application tables."""
    bind = target_engine or engine
    Base.metadata.create_all(bind=bind)
    _ensure_sqlite_column(bind, "leads", "smallest_first_version", "TEXT")


def _ensure_sqlite_column(bind, table: str, column: str, ddl_type: str) -> None:
    url = str(getattr(bind, "url", "") or "")
    if not url.startswith("sqlite"):
        return
    from sqlalchemy import inspect, text

    inspector = inspect(bind)
    if table not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns(table)}
    if column in existing:
        return
    with bind.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
