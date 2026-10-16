"""Clients for the systems outside this app: Gmail, Google OAuth, the auth service, Redis.

Services call these; they never build API requests or publish to Redis themselves.
(Our own database is not a client: tables in app/models, queries in app/repositories.)
"""
