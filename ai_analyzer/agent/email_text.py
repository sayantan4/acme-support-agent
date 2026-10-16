"""Only the new part of an email reply: Gmail appends the whole earlier thread as quoted text.

    "Thanks\n\nOn Fri, 2 Oct, 2026, 21:50 , <support@...> wrote:\n> Hi Rahul, ..."  ->  "Thanks"

Without this a one-word "Thanks" looks like a deposit question (the quoted reply is about a deposit),
so retrieval finds deposit SOPs and the agent answers the deposit again.
"""

import re

# "On <date>, <name> <email> wrote:", sometimes wrapped onto a second line by the mail client.
QUOTE_HEADER = re.compile(r"^On\b[^\n]*(?:\n[^\n]*)?\bwrote:\s*$", re.MULTILINE)


def new_text(body: str) -> str:
    text = body.replace("\r\n", "\n")

    header = QUOTE_HEADER.search(text)
    if header:
        text = text[:header.start()]

    lines = [line for line in text.split("\n") if not line.lstrip().startswith(">")]
    new = "\n".join(lines).strip()
    return new or body.strip()  # a message that is all quote: keep it rather than send nothing
