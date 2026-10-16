"""Business rules. Called by controllers; use app/repositories for the database and app/clients for
everything outside (Gmail, Redis, the auth service).

A service owns its transaction: it opens session_scope() and passes the session to repositories.
"""
