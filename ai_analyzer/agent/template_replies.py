"""Replies for messages that don't need the agent (scope gate said no_issue or out_of_scope): fixed texts.

Templates can't invent products or promises and cost nothing. Each kind is sent at most once in a row:

    no_issue / greeting       "How can I help with your Acme Invest account?"   reply_kind small_talk
    no_issue / thanks_solved  "Glad I could help. Have a nice day!"             reply_kind closing
    no_issue / thanks_open    "Thanks for your patience, our team will update"  reply_kind closing
    no_issue / noise          (no reply)
    out_of_scope              "Acme Invest doesn't offer X. We can help with..." reply_kind out_of_scope

No reply ("ignore") when: our last reply was the same kind (closing after closing, small talk after small
talk, out of scope after out of scope: the first one already says what Acme helps with), a human owns the case (our last reply was a holding reply), or the text would repeat our last reply.
The message is still stored and summarized; only the email isn't sent.
"""

import re

from ai_analyzer.agent.loop import AgentResult
from ai_analyzer.agent.scope import SCOPE_PROMPT_VERSION, ScopeDecision

SIGN_OFF = "\n\nBest regards,\nAcme Invest Support"
TEMPLATES = {
    "greeting": ("small_talk", "Hi! I'm doing well, thank you. How can I help with your Acme Invest account today?"),
    "thanks_solved": ("closing", "Glad I could help. Have a nice day!"),
    "thanks_open": ("closing", "Thanks for your patience. Our team will update you as soon as there is news."),
}
OUT_OF_SCOPE = ("Hi, thanks for writing to us. Acme Invest doesn't offer {topics}, so we can't help with that "
                "here; please contact the provider of that service. We're happy to help with your Acme Invest "
                "account: deposits and withdrawals, crypto trading, KYC, fees, rewards, tax statements and "
                "account security.")
ONCE_IN_A_ROW = {"closing", "small_talk", "out_of_scope"}


def reply_without_agent(decision: ScopeDecision, previous_replies: list[str], last_reply_kind: str | None,
                        reply_to_message_id: int | None) -> AgentResult:
    """The run's result for a no_issue / out_of_scope message: the gate's summary and a template reply (or none)."""
    result = AgentResult(summary=decision.summary, reply=None, escalate=False, escalation_note=None,
                         sop_ids_used=[], reply_to_message_id=reply_to_message_id, model=decision.model,
                         prompt_version=f"{SCOPE_PROMPT_VERSION}+template")
    result.reply_kind, text = _template(decision)
    if text is None:
        result.reply_kind, result.reply_dropped = "none", "nothing to answer (empty or noise)"
        return result

    reason = _why_not_send(result.reply_kind, text, previous_replies, last_reply_kind)
    if reason:
        result.reply_kind, result.reply_dropped = "none", reason
        return result
    result.reply = text
    return result


def _template(decision: ScopeDecision) -> tuple[str, str | None]:
    if decision.decision == "out_of_scope":
        return "out_of_scope", OUT_OF_SCOPE.format(topics=_topics(decision.out_of_scope_topics)) + SIGN_OFF
    if decision.no_issue_kind in TEMPLATES:
        kind, text = TEMPLATES[decision.no_issue_kind]
        return kind, text + SIGN_OFF
    return "none", None  # noise


def _why_not_send(kind: str, text: str, previous_replies: list[str], last_reply_kind: str | None) -> str | None:
    """Why this template must not be sent now, or None if it may be."""
    if last_reply_kind == "holding" and kind != "out_of_scope":
        return "a human owns this case (our last reply was a holding reply)"
    if kind in ONCE_IN_A_ROW and last_reply_kind == kind:
        return f"our last reply was already {kind.replace('_', ' ')}"
    if previous_replies and previous_replies[-1].strip() == text.strip():
        return "same text as our last reply"
    return None


def _topics(topics: list[str]) -> str:
    """The gate's topic words, made safe to put in an email: letters, digits, spaces, & and -; at most 3."""
    cleaned = []
    for topic in topics[:3]:
        # Other characters become spaces, so "loan/credit products" stays two words.
        safe = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 &\-]", " ", topic)).strip()[:40]
        if safe:
            cleaned.append(safe)
    if not cleaned:
        return "that"
    return cleaned[0] if len(cleaned) == 1 else ", ".join(cleaned[:-1]) + " or " + cleaned[-1]
