"""Controller: the HTTP API of ToolSvc.

    GET  /tools                -> [{"name", "description", "parameters", "required"}, ...]
    POST /tools/<name>         {"message_id": 57, "arguments": {...}}
                               -> 200 {"result": ...}
                               -> 200 {"error": "..."}  tool-level problem, e.g. not found
                               -> 400 bad request, 403 message has no verified customer

The user is never taken from the request: ToolSvc looks up the message's verified customer in the
main app for every call, so several emails being handled at once can't mix up their customers.
Only answers requests from this machine for now; use service auth (token / mTLS) beyond localhost.
"""

from flask import Blueprint, Flask, abort, jsonify, request

from toolsvc.config import get_settings, is_trusted
from toolsvc.services import tool_service

bp = Blueprint("tools", __name__)


@bp.before_request
def only_from_localhost():
    if not is_trusted(request.remote_addr, get_settings().trusted_networks):
        abort(403)


@bp.get("/tools")
def list_tools():
    return jsonify(tool_service.list_tools())


@bp.post("/tools/<name>")
def run_tool(name: str):
    body = request.get_json(silent=True) or {}
    message_id, arguments = body.get("message_id"), body.get("arguments", {})
    if not isinstance(message_id, int) or not isinstance(arguments, dict):
        return jsonify({"error": "body must be {\"message_id\": <int>, \"arguments\": {...}}"}), 400

    try:
        result = tool_service.run_for_message(name, message_id, arguments)
    except tool_service.NoVerifiedCustomer:
        return jsonify({"error": f"message {message_id} has no verified customer"}), 403
    return jsonify(result)


@bp.get("/healthz")
def healthz():
    return {"ok": True}


def create_app() -> Flask:
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app
