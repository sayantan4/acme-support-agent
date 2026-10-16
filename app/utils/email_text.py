"""Read and write emails. Pure functions, no I/O.

Reading: the readable text and a few headers of a Gmail API message (format="full").
Writing: a reply with the headers that make Gmail thread it under the original.
"""

import base64
import re
from email.message import EmailMessage
from html import unescape

MAX_BODY_CHARS = 8000  # keeps LLM input (and cost) bounded for very long emails
NO_REPLY_WORDS = ("noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "notifications@")


def header(message: dict, name: str) -> str | None:
    """The first header with this name (case-insensitive), or None."""
    for item in message.get("payload", {}).get("headers", []):
        if item["name"].lower() == name.lower():
            return item["value"]
    return None


def is_automated(message: dict) -> bool:
    """True for mail sent by a machine: newsletters, notifications, auto-replies."""
    sender = (header(message, "From") or "").lower()
    precedence = (header(message, "Precedence") or "").lower()
    auto_submitted = (header(message, "Auto-Submitted") or "no").lower()
    return (
        any(word in sender for word in NO_REPLY_WORDS)
        or header(message, "List-Unsubscribe") is not None  # mailing lists and newsletters
        or precedence in ("bulk", "list", "junk")
        or auto_submitted != "no"  # RFC 3834: auto-replies, auto-generated mail
    )


def body_text(message: dict) -> str:
    """Prefer the text/plain part; fall back to text/html with tags stripped."""
    payload = message.get("payload", {})
    text = _find_part(payload, "text/plain")
    if text is None:
        html = _find_part(payload, "text/html")
        text = _html_to_text(html) if html else message.get("snippet", "")
    return text.strip()[:MAX_BODY_CHARS]


def _find_part(part: dict, mime_type: str) -> str | None:
    """Depth-first search of the MIME tree for the first part of this type."""
    if part.get("mimeType") == mime_type and part.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
    for child in part.get("parts", []):
        found = _find_part(child, mime_type)
        if found is not None:
            return found
    return None


def _html_to_text(html: str) -> str:
    """Drop scripts, styles and tags, then collapse whitespace. Rough, but enough for an LLM."""
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", unescape(text))


def build_reply(mailbox: str, to: str, subject: str, body: str, original_message_id: str | None,
                original_references: str | None) -> EmailMessage:
    """The reply as an email, with the headers that make Gmail thread it under the original."""
    reply = EmailMessage()
    reply["From"] = mailbox
    reply["To"] = to
    reply["Subject"] = subject
    if original_message_id:
        reply["In-Reply-To"] = original_message_id
        # References = the original's own chain, plus the original itself
        if original_references:
            reply["References"] = f"{original_references} {original_message_id}".strip()
        else:
            reply["References"] = original_message_id
    reply.set_content(body)
    return reply
