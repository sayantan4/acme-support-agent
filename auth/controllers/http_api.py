"""Controller: the HTTP API of the auth user_service.

    POST /register/user  {"email_id": "...", "user_id": 101?}  -> 201 {user}, 409 if already registered
    POST /verify/user    {"email_id": "..."}                    -> 200 {user} (check is_active),
                                                                   404 if unknown

Only answers requests from this machine for now; put it behind service auth (token / mTLS) before
anything else can reach it.
"""

from flask import Blueprint, Flask, abort, jsonify, request

from auth.config import get_settings, is_trusted
from auth.models.session import init_db
from auth.services import user_service

bp = Blueprint("auth", __name__)


@bp.before_request
def only_from_localhost():
    if not is_trusted(request.remote_addr, get_settings().trusted_networks):
        abort(403)


def email_from_body() -> str:
    body = request.get_json(silent=True) or {}
    email = body.get("email_id")
    if not isinstance(email, str) or not email.strip():
        abort(400, description="email_id is required")
    return email


@bp.post("/register/user")
def register_user():
    body = request.get_json(silent=True) or {}
    try:
        user = user_service.register_user(email_from_body(), user_id=body.get("user_id"))
    except user_service.AlreadyRegistered as already:
        return jsonify({"error": f"already registered: {already}"}), 409
    except ValueError as bad:
        return jsonify({"error": str(bad)}), 400
    return jsonify(user), 201


@bp.post("/verify/user")
def verify_user():
    user = user_service.verify_user(email_from_body())
    if user is None:
        return jsonify({"error": "email is not registered"}), 404
    return jsonify(user), 200


@bp.get("/healthz")
def healthz():
    return {"ok": True}


def create_app() -> Flask:
    init_db()
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app
