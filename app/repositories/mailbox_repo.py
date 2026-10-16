"""Queries on mailboxes (connected Gmail accounts). Every function takes the caller's session."""

from sqlalchemy.orm import Session

from app.models import Mailbox


def get(session: Session, email: str) -> Mailbox | None:
    return session.get(Mailbox, email)


def add(session: Session, mailbox: Mailbox) -> None:
    session.add(mailbox)


def all_mailboxes(session: Session) -> list[Mailbox]:
    return session.query(Mailbox).all()


def active_emails(session: Session) -> list[str]:
    rows = session.query(Mailbox.email).filter(
        Mailbox.status == "active",
    ).all()
    return [row.email for row in rows]
