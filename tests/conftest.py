"""Test setup: throwaway Postgres databases, and settings that keep tests away from real services.

Tests run against Postgres, like production. Each test run creates its own three databases
(main app, auth, ai_analyzer) with a unique name and drops them at the end, so runs never see each
other's data or touch the real databases. Needs a Postgres server: TEST_POSTGRES_URL, by default the
docker compose one (`docker compose up -d postgres`, published on localhost:5433). For a local Postgres:
TEST_POSTGRES_URL=postgresql+psycopg://localhost:5432/postgres
"""

import os
import uuid

from sqlalchemy import create_engine, text

ADMIN_URL = os.environ.get("TEST_POSTGRES_URL", "postgresql+psycopg://acme:acme@localhost:5433/postgres")
RUN_ID = uuid.uuid4().hex[:8]
TEST_DATABASES = {
    "DATABASE_URL": f"test_{RUN_ID}_main",
    "AUTH_DATABASE_URL": f"test_{RUN_ID}_auth",          # the auth service has its own database
    "ANALYZER_DATABASE_URL": f"test_{RUN_ID}_analyzer",  # ai_analyzer's own (agent_runs, reviews)
}

# CREATE / DROP DATABASE can't run inside a transaction.
_admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")


def _database_url(name: str) -> str:
    return ADMIN_URL.rsplit("/", 1)[0] + f"/{name}"


# Configure before any app module reads settings.
with _admin.connect() as conn:
    for env_name, database in TEST_DATABASES.items():
        conn.execute(text(f'CREATE DATABASE "{database}"'))
        os.environ[env_name] = _database_url(database)

os.environ["PUBSUB_VERIFY_JWT"] = "false"
os.environ["OAUTH_REDIRECT_URI"] = "http://localhost:8000/oauth/callback"
# Never send real email from tests, whatever .env says. Tests that need sending turn it on with a fake Gmail.
os.environ["SEND_REPLIES"] = "false"
# Never call a paid LLM from tests: with a fake key, a call that a test forgot to stub fails at once.
os.environ["OPENAI_API_KEY"] = "sk-test-not-a-real-key"


def pytest_sessionfinish(session, exitstatus):
    """Drop this run's databases. The app engines still hold pooled connections, so close them first."""
    import ai_analyzer.models.session
    import app.models.session
    import auth.models.session

    for module in (app.models.session, auth.models.session, ai_analyzer.models.session):
        module.engine.dispose()
    with _admin.connect() as conn:
        for database in TEST_DATABASES.values():
            conn.execute(text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
    _admin.dispose()
