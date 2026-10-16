"""Auth service: which email address belongs to which customer (user_id).

Its own database and its own API; it imports nothing from app/, ai_analyzer/ or toolsvc/.

    uv run python -m auth serve                          # API on :8001
    uv run python -m auth seed                           # register the demo customers (ids 101-106)
    uv run python -m auth register you@gmail.com --user-id 101
"""
