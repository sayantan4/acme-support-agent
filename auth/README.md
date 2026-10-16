# Auth service

Maps a customer's email address to their `user_id`. Own Postgres database (`auth`,
`AUTH_DATABASE_URL`) and own API; imports nothing from the other services.

```
uv run python -m auth serve                                 # API on :8001 (AUTH_PORT)
uv run python -m auth seed                                  # demo customers 101-106 (match toolsvc's fake data)
uv run python -m auth register you@gmail.com --user-id 101  # test with your own Gmail as customer 101
uv run python -m auth deactivate 101 | activate 101
```

| Endpoint | |
|---|---|
| `POST /register/user` `{"email_id", "user_id"?}` | 201 user; 409 already registered; 400 not an email |
| `POST /verify/user` `{"email_id"}` | 200 user (check `is_active`); 404 not registered |

Table `users`: `user_id`, `email_id` (unique, lowercase), `is_active`, `created_at`, `updated_at`.

| Layer | Files |
|---|---|
| controllers | `controllers/http_api.py` (routes), `controllers/cli.py` (`python -m auth …`) |
| services | `services/user_service.py`: register, verify, activate, seed |
| repositories | `repositories/user_repo.py`: all SQL |
| models | `models/user.py`, `models/session.py` (engine, `session_scope`) |

**Who calls it:** the main app's handler, for every new email (`app/clients/auth_client.py`). Strict for now:
only a registered, active sender gets `processed_messages.user_id` set and is summarized / replied to.
An unknown sender is stored (dedupe) and left alone. If auth is down the handler raises, so the email
is retried on the next sync.

**Not done yet:** this trusts the `From:` address. Before real use, also require a DKIM/DMARC pass
(Gmail's `Authentication-Results` header), since `From:` can be forged. And put service auth
(token / mTLS) in front of the API; today it only answers localhost.
