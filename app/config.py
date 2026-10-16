"""Settings, read from the environment / .env (e.g. DATABASE_URL -> database_url)."""

import ipaddress
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://localhost:5432/gmail_webhooks"
    base_url: str = "http://localhost:8000"

    google_client_secrets_file: str = "./client_secret.json"
    oauth_redirect_uri: str = "http://localhost:8000/oauth/callback"
    gmail_scopes: str = "https://www.googleapis.com/auth/gmail.readonly"

    pubsub_topic: str = ""
    # Pull mode only: projects/<id>/subscriptions/<name>
    pubsub_subscription: str = ""
    pubsub_verify_jwt: bool = True
    pubsub_audience: str = ""
    pubsub_sa_email: str = ""

    token_encryption_key: str = ""
    redis_url: str = "redis://localhost:6379/0"
    full_sync_max_messages: int = 50
    # How often the summary scheduler sends waiting messages to C1, in seconds
    # (30 while testing; 300 normally)
    summary_batch_seconds: int = 30
    # The auth service, which maps a sender's email to a customer user_id
    auth_url: str = "http://localhost:8001"
    # Send the agent's replies through Gmail (needs the gmail.send scope). Off = store them only.
    send_replies: bool = False
    # /internal/* also answers these networks (comma-separated CIDRs), besides localhost
    trusted_networks: str = ""

    @property
    def scopes(self) -> list[str]:
        return [scope.strip() for scope in self.gmail_scopes.split(",") if scope.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def is_trusted(remote_addr: str | None, trusted_networks: str) -> bool:
    """Loopback, or inside one of the comma-separated CIDRs (e.g. the docker compose network)."""
    if not remote_addr:
        return False
    addr = ipaddress.ip_address(remote_addr)
    if addr.is_loopback:
        return True
    for network in trusted_networks.split(","):
        if network.strip() and addr in ipaddress.ip_network(network.strip()):
            return True
    return False
