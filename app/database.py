from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def initialize_database() -> None:
    """Create tables and apply the prototype's backward-compatible SQLite additions."""

    Base.metadata.create_all(bind=engine)
    if not settings.database_url.startswith("sqlite"):
        return

    claim_columns = {column["name"] for column in inspect(engine).get_columns("claims")}
    if "amount" not in claim_columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE claims ADD COLUMN amount NUMERIC(10, 2) NOT NULL DEFAULT 0")
            )
    if "date_of_service" not in claim_columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE claims ADD COLUMN date_of_service DATE "
                    "NOT NULL DEFAULT '2026-08-01'"
                )
            )
    nullable_columns = {
        "auth_number_in_chart": "VARCHAR(50)",
        "modifier_attached": "VARCHAR(20)",
        "diagnosis_code": "VARCHAR(20)",
        "eob_notes": "TEXT",
        "policy_confidence": "NUMERIC(5, 2)",
    }
    for column_name, column_type in nullable_columns.items():
        if column_name not in claim_columns:
            with engine.begin() as connection:
                connection.execute(
                    text(f"ALTER TABLE claims ADD COLUMN {column_name} {column_type}")
                )


def get_db() -> Generator[Session, None, None]:
    """Provide one database session per request."""

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
