"""The connection to ai_analyzer's Postgres database, and session_scope() for transactions.

Services own the transaction: a service opens session_scope() and passes the session to the
repositories it calls (ai_analyzer/repositories). Repositories never commit.
"""

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from ai_analyzer.config import get_settings
from ai_analyzer.models import Base  # also registers every table, for create_all()

engine = create_engine(get_settings().analyzer_database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


# create_all() never changes existing tables. Until there is Alembic, these idempotent statements bring
# tables made by older code up to date; each is a no-op when already applied.
UPGRADES = [
    "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS scope_decision VARCHAR(20)",
    "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS scope_reason TEXT",
    "ALTER TABLE agent_runs ALTER COLUMN reply_kind TYPE VARCHAR(20)",
    "ALTER TABLE eval_agent_cases ALTER COLUMN reply_kind TYPE VARCHAR(20)",
    "ALTER TABLE eval_gate_cases ADD COLUMN IF NOT EXISTS message TEXT",
    "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS customer_message TEXT",
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
