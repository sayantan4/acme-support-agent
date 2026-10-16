"""The Flask app: wires the HTTP controllers together.

    uv run flask --app app.main:create_app run --port 8000

Layers:  controllers/ (routes, CLI, workers)  ->  services/ (business rules)
           ->  repositories/ (SQL)  ->  models/ (tables)  ->  Postgres
           ->  clients/ (Gmail, Google OAuth, auth service, Redis)  ->  the outside world
"""

import logging

from flask import Flask

from app.controllers import http_internal, http_mailboxes, http_oauth, http_webhook
from app.models.session import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app() -> Flask:
    """Create the tables if needed and build the app with every controller registered."""
    init_db()
    app = Flask(__name__)
    app.register_blueprint(http_webhook.bp)
    app.register_blueprint(http_oauth.bp)
    app.register_blueprint(http_internal.bp)
    app.register_blueprint(http_mailboxes.bp)
    return app
