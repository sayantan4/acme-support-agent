"""Google's OAuth consent flow (google_auth_oauthlib): the consent URL, and code -> tokens."""

import os

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from app.config import get_settings

# Google may return scopes in a different order/superset; don't fail on that.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
if get_settings().oauth_redirect_uri.startswith("http://localhost"):
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")  # local dev only


def authorization_url(state: str) -> tuple[str, str | None]:
    """The consent URL to send the user to, and the PKCE code_verifier to keep for the callback."""
    flow = _flow()
    url, _ = flow.authorization_url(
        access_type="offline",  # we need a refresh token
        prompt="consent",       # force refresh token even if previously granted
        include_granted_scopes="true",
        state=state,
    )
    return url, flow.code_verifier


def exchange_code(state: str, code: str, code_verifier: str | None) -> Credentials:
    """Trade the code from Google's redirect for the user's tokens."""
    flow = _flow(state)
    flow.code_verifier = code_verifier
    flow.fetch_token(code=code)
    return flow.credentials


def _flow(state: str | None = None) -> Flow:
    settings = get_settings()
    return Flow.from_client_secrets_file(
        settings.google_client_secrets_file,
        scopes=settings.scopes,
        redirect_uri=settings.oauth_redirect_uri,
        state=state,
    )
