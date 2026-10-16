"""Register and verify customers. The HTTP API and the CLI (auth/controllers) both call these."""

from email.utils import parseaddr

from auth.models import User
from auth.models.session import session_scope
from auth.repositories import user_repo

# The demo customers in toolsvc's fake database (toolsvc/models/fake_db.py). `python -m auth seed` registers
# them with these exact ids, so a verified email leads to a user who has data to look at.
DEMO_USERS = {
    101: "rahul.mehta@example.com",
    102: "priya.shah@example.com",
    103: "amit.k@example.com",
    104: "neha.r@example.com",
    105: "vikram.s@example.com",
    106: "sneha.p@example.com",
}


class AlreadyRegistered(Exception):
    """The email, or the requested user_id, is already taken."""


def normalize_email(raw: str) -> str:
    """'Rahul <Rahul.Mehta@Example.com>' -> 'rahul.mehta@example.com'."""
    return parseaddr(raw)[1].strip().lower()


def as_dict(user: User) -> dict:
    return {
        "user_id": user.user_id,
        "email_id": user.email_id,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat(),
        "updated_at": user.updated_at.isoformat(),
    }


def register_user(email: str, user_id: int | None = None) -> dict:
    """Create a customer. user_id is normally assigned; pass one only to line up with demo data."""
    email_id = normalize_email(email)
    if not email_id or "@" not in email_id:
        raise ValueError(f"not an email address: {email!r}")
    with session_scope() as session:
        if user_repo.find_by_email(session, email_id) is not None:
            raise AlreadyRegistered(email_id)
        if user_id is not None and user_repo.get(session, user_id) is not None:
            raise AlreadyRegistered(f"user_id {user_id}")
        user = User(email_id=email_id) if user_id is None else User(email_id=email_id, user_id=user_id)
        return as_dict(user_repo.add(session, user))


def verify_user(email: str) -> dict | None:
    """The customer this email belongs to, or None if it isn't registered.

    Inactive users are returned too, with is_active false, so the caller decides; the main app treats
    them as not verified.
    """
    with session_scope() as session:
        user = user_repo.find_by_email(session, normalize_email(email))
        return as_dict(user) if user else None


def set_active(user_id: int, is_active: bool) -> dict | None:
    """Activate or deactivate a customer. None if there is no such user_id."""
    with session_scope() as session:
        user = user_repo.get(session, user_id)
        if user is None:
            return None
        user.is_active = is_active
        session.flush()  # sets updated_at
        return as_dict(user)


def seed_demo_users() -> list[dict]:
    """Register DEMO_USERS, skipping any already there. Safe to run more than once."""
    created = []
    for user_id, email in DEMO_USERS.items():
        try:
            created.append(register_user(email, user_id=user_id))
        except AlreadyRegistered:
            pass
    return created
