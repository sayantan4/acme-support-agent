"""Queries on users. Every function takes the caller's session."""

from sqlalchemy.orm import Session

from auth.models import User


def get(session: Session, user_id: int) -> User | None:
    return session.get(User, user_id)


def find_by_email(session: Session, email_id: str) -> User | None:
    """email_id must already be normalized (that is how it is stored)."""
    return session.query(User).filter(
        User.email_id == email_id,
    ).first()


def add(session: Session, user: User) -> User:
    """Add and flush, so user.user_id is assigned."""
    session.add(user)
    session.flush()
    return user
