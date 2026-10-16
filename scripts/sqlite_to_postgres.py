"""One-off: copy the data from the old SQLite files into the Postgres databases.

    PYTHONPATH=. uv run python scripts/sqlite_to_postgres.py app  gmail_webhooks.db  postgresql+psycopg://localhost:5432/gmail_webhooks
    PYTHONPATH=. uv run python scripts/sqlite_to_postgres.py auth auth.db            postgresql+psycopg://localhost:5432/auth

Stop the services first, so nothing writes to SQLite during the copy. Tables are created from the
SQLAlchemy models; rows keep their ids (summaries and replies point at processed_messages.id), and each
id sequence is moved past the copied ids. Refuses to copy into a table that already has rows.
"""

import argparse
from datetime import datetime, timezone

from sqlalchemy import Integer, MetaData, Table, create_engine, func, inspect, select, text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("service", choices=["app", "auth"], help="whose models describe the tables")
    parser.add_argument("sqlite_file")
    parser.add_argument("postgres_url")
    args = parser.parse_args()

    metadata = models_of(args.service)
    source = create_engine(f"sqlite:///{args.sqlite_file}")
    target = create_engine(args.postgres_url)

    metadata.create_all(target)
    for table in metadata.sorted_tables:  # parents before children
        copied = copy_table(table, source, target)
        print(f"{table.name}: {copied} row(s)")


def models_of(service: str) -> MetaData:
    if service == "app":
        from app.models import Base
    else:
        from auth.models import Base
    return Base.metadata


def copy_table(table: Table, source, target) -> int:
    if table.name not in inspect(source).get_table_names():
        return 0
    with target.connect() as conn:
        if conn.execute(select(func.count()).select_from(table)).scalar():
            raise SystemExit(f"{table.name} already has rows in Postgres; not copying twice")

    rows = read_rows(table, source)
    with target.begin() as conn:
        if rows:
            conn.execute(table.insert(), rows)
        move_sequence_past_copied_ids(conn, table)
    return len(rows)


def read_rows(table: Table, source) -> list[dict]:
    """Every row, with only the columns both sides have, and datetimes marked as UTC."""
    source_columns = {column["name"] for column in inspect(source).get_columns(table.name)}
    columns = [column for column in table.columns if column.name in source_columns]
    with source.connect() as conn:
        rows = conn.execute(select(*columns)).mappings().all()
    return [{name: as_utc(value) for name, value in row.items()} for row in rows]


def as_utc(value):
    # SQLite drops the timezone: our timestamps were written as UTC (db.utcnow), so say so again.
    # Without this, Postgres would read them as local time (IST) and shift them by 5h30.
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def move_sequence_past_copied_ids(conn, table: Table) -> None:
    """Rows were inserted with their old ids, so the id sequence must continue after the largest one."""
    for column in table.primary_key.columns:
        if isinstance(column.type, Integer) and column.autoincrement in (True, "auto"):
            conn.execute(text(
                f"SELECT setval(pg_get_serial_sequence('{table.name}', '{column.name}'), "
                f"COALESCE((SELECT MAX({column.name}) FROM {table.name}), 0) + 1, false)"
            ))


if __name__ == "__main__":
    main()
