"""Controller: operational commands. Schedule `renew-watches` daily and `sync-all` every ~15-30 min.

    python -m app.cli pull              # pull-mode worker (no public URL needed)
    python -m app.cli summary-scheduler # sends waiting messages to C1 in batches (--once for cron)
    python -m app.cli summary-writer    # C2: saves summaries from ai_analyzer
    python -m app.cli renew-watches
    python -m app.cli sync-all
    python -m app.cli sync you@example.com
    python -m app.cli stop you@example.com
    python -m app.cli approvals you@example.com   # escalated replies waiting for a human
    python -m app.cli approve <reply_id>
    python -m app.cli reject <reply_id>
"""

import argparse
import json
import logging

from app.models.session import init_db
from app.services import reply_service, summary_service, sync_service, watch_service


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = _parser().parse_args()
    init_db()
    result = _run(args)
    if result is not None:  # long-running workers return None and print nothing
        print(json.dumps(result, indent=2, default=str))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    sub.add_parser("pull", help="run the pull-mode worker (reads notifications from Pub/Sub)")

    scheduler = sub.add_parser("summary-scheduler", help="send pending messages to C1 in batches")
    scheduler.add_argument("--once", action="store_true", help="send one batch and exit (for cron)")
    scheduler.add_argument("--every", type=int,
                           help="seconds between batches (default SUMMARY_BATCH_SECONDS)")
    sub.add_parser("summary-writer", help="C2: save finished summaries to thread_summary_info")

    sub.add_parser("renew-watches", help="renew every active mailbox's watch (run daily)")
    sub.add_parser("sync-all", help="safety-net sync of every active mailbox")
    sub.add_parser("sync", help="sync one mailbox").add_argument("email")
    sub.add_parser("stop", help="stop watching and mark disconnected").add_argument("email")

    sub.add_parser("approvals", help="list escalated replies waiting for approval").add_argument("email")
    sub.add_parser("approve", help="approve an escalated reply: C3 sends it").add_argument(
        "reply_id", type=int)
    sub.add_parser("reject", help="reject an escalated reply: it is never sent").add_argument(
        "reply_id", type=int)
    return parser


def _run(args: argparse.Namespace):
    """Run one command. Returns what to print, or None for the long-running workers."""
    if args.cmd == "init-db":
        return "ok"  # init_db() already ran in main()
    if args.cmd == "pull":
        from app.controllers.pubsub_worker import run

        run()
        return None
    if args.cmd == "summary-scheduler":
        if args.once:
            return {"requests_sent": summary_service.send_pending_summary_requests()}
        from app.controllers.stream_workers import run_summary_scheduler

        run_summary_scheduler(args.every)
        return None
    if args.cmd == "summary-writer":
        from app.controllers.stream_workers import run_summary_writer

        run_summary_writer()
        return None
    if args.cmd == "renew-watches":
        return watch_service.renew_all_watches()
    if args.cmd == "sync-all":
        return sync_service.sync_all_active()
    if args.cmd == "sync":
        return {args.email: sync_service.sync_mailbox(args.email)}
    if args.cmd == "stop":
        try:
            watch_service.disconnect(args.email)
        except ValueError as problem:
            raise SystemExit(str(problem))
        return "stopped"
    if args.cmd == "approvals":
        return _approvals(args.email)
    if args.cmd == "approve":
        reply_service.approve_reply(args.reply_id)
        return {"approved": args.reply_id, "next": "C3 sends it"}
    if args.cmd == "reject":
        reply_service.reject_reply(args.reply_id)
        return {"rejected": args.reply_id}


def _approvals(email: str) -> list[dict]:
    approvals = []
    for reply in reply_service.awaiting_approval(email):
        approvals.append({
            "reply_id": reply.id,
            "thread_id": reply.thread_id,
            "in_reply_to_id": reply.in_reply_to_id,
            "subject": reply.subject,
            "reply": reply.body_text,
        })
    return approvals
