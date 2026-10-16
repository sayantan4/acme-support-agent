"""Auth service settings (AUTH_* env vars) and the check for who may call its API."""

import ipaddress
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Shares the repo's .env for now; variables are prefixed AUTH_ so they don't clash.
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AUTH_", extra="ignore")

    database_url: str = "postgresql+psycopg://localhost:5432/auth"   # AUTH_DATABASE_URL
    host: str = "127.0.0.1"                     # AUTH_HOST (0.0.0.0 in a container)
    port: int = 8001                            # AUTH_PORT
    trusted_networks: str = ""                  # AUTH_TRUSTED_NETWORKS: CIDRs allowed besides localhost


@lru_cache
def get_settings() -> Settings:
    return Settings()


def is_trusted(remote_addr: str | None, trusted_networks: str) -> bool:
    """Loopback, or inside one of the comma-separated CIDRs (e.g. the docker compose network)."""
    if not remote_addr:
        return False
    address = ipaddress.ip_address(remote_addr)
    if address.is_loopback:
        return True
    cidrs = [cidr.strip() for cidr in trusted_networks.split(",") if cidr.strip()]
    return any(address in ipaddress.ip_network(cidr) for cidr in cidrs)
