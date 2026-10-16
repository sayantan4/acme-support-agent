"""The auth service's own Postgres table (AUTH_DATABASE_URL): users. Queries in auth/repositories."""

from auth.models.base import Base
from auth.models.user import User

__all__ = ["Base", "User"]
