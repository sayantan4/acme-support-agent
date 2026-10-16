"""Controller: connect a Gmail account. GET /oauth/start -> Google consent -> GET /oauth/callback."""

from flask import Blueprint, abort, redirect, request

from app.services import mailbox_service

bp = Blueprint("oauth", __name__, url_prefix="/oauth")


@bp.get("/start")
def oauth_start():
    return redirect(mailbox_service.start_connect())


@bp.get("/callback")
def oauth_callback():
    if error := request.args.get("error"):
        abort(400, f"OAuth error: {error}")
    try:
        return mailbox_service.finish_connect(request.args.get("state"), request.args.get("code"))
    except mailbox_service.ConnectError as problem:
        abort(400, str(problem))
