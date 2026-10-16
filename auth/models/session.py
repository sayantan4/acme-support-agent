"""The connection to the auth service's database, and session_scope() for transactions.

Services own the transaction and pass the session to repositories, which never commit.
"""

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from auth.config import get_settings
from auth.models import Base  # also registers the table, for create_all()

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    """Create missing tables. Safe to run on every start."""
    Base.metadata.create_all(engine)


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
