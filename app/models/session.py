"""The connection to Postgres, and session_scope() for transactions.

DATABASE_URL, e.g. postgresql+psycopg://localhost:5432/gmail_webhooks. init_db() creates missing tables;
it never changes existing ones (a schema change needs a migration; Alembic is the SQLAlchemy tool).

Services own the transaction: a service opens session_scope() and passes the session to the
repositories it calls (app/repositories). Repositories never commit.
"""

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import Base  # also registers every table, for create_all()

engine = create_engine(
    get_settings().database_url,
    pool_pre_ping=True,  # a pooled connection Postgres closed (restart, idle timeout) is replaced, not used
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


# create_all() never changes existing tables. Until there is Alembic, these idempotent statements bring
# tables made by older code up to date; each is a no-op when already applied.
UPGRADES = [
    "ALTER TABLE processed_messages ALTER COLUMN reply_kind TYPE VARCHAR(20)",
]


def init_db() -> None:
    """Create missing tables and upgrade older ones. Safe to run on every start."""
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for statement in UPGRADES:
            conn.execute(text(statement))


@contextmanager
def session_scope() -> Iterator[Session]:
    """A session that commits when the block ends, or rolls back if it raises."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
