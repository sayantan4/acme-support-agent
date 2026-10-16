"""Controller: the auth CLI.

    python -m auth serve | seed | register <email> [--user-id N] | deactivate <user_id> | activate <user_id>
"""

import argparse
import json
import logging

from auth.config import get_settings
from auth.models.session import init_db
from auth.services import user_service


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args()

    init_db()
    if args.cmd == "serve":
        from auth.controllers.http_api import create_app

        create_app().run(host=get_settings().host, port=get_settings().port)
        return
    print(json.dumps(run_command(args), indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="auth")
    commands = parser.add_subparsers(dest="cmd", required=True)
    commands.add_parser("serve", help="run the auth API")
    commands.add_parser("seed", help="register the demo customers 101-106 (matches toolsvc's fake data)")
    register = commands.add_parser("register", help="register an email, e.g. your own Gmail for live testing")
    register.add_argument("email")
    register.add_argument("--user-id", type=int,
                          help="use this id (e.g. 101 to see demo customer 101's data)")
    commands.add_parser("deactivate").add_argument("user_id", type=int)
    commands.add_parser("activate").add_argument("user_id", type=int)
    return parser


def run_command(args: argparse.Namespace):
    """Run one of the one-shot commands (everything but serve) and return what to print."""
    if args.cmd == "seed":
        return user_service.seed_demo_users()
    if args.cmd == "register":
        return user_service.register_user(args.email, user_id=args.user_id)
    return user_service.set_active(args.user_id, is_active=args.cmd == "activate")

