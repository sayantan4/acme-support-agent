"""agent_runs: every agent run. What it retrieved, which tools it called and what they returned, its
reply (or none), tokens and time. A reviewer reads this to judge the reply.

reply_id is the main app's processed_messages.id of the stored reply (another service's id, so no
foreign key).
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ai_analyzer.models.base import Base, utcnow


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # main app's processed_messages.id of our reply; NULL until C3 stores it (or forever: no reply)
    reply_id: Mapped[Optional[int]] = mapped_column(Integer, unique=True)
    in_reply_to_id: Mapped[Optional[int]] = mapped_column(Integer)  # the customer message answered
    # no_reply | published (C1 sent it to C3) | stored (main app has it) | dropped (newer message arrived)
    outcome: Mapped[str] = mapped_column(String(10))
    mailbox: Mapped[str] = mapped_column(String(320))
    thread_id: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    sops_retrieved: Mapped[list] = mapped_column(JSON)  # [{"sop_id": "TRD-05", "grade": 2}, ...]
    sops_followed: Mapped[list] = mapped_column(JSON)   # ["TRD-05", ...]
    tool_calls: Mapped[list] = mapped_column(JSON)      # [{"turn", "name", "arguments", "ok", "output"}, ...]
    summary: Mapped[str] = mapped_column(Text)
    reply: Mapped[Optional[str]] = mapped_column(Text)  # NULL: the agent decided not to reply
    reply_kind: Mapped[Optional[str]] = mapped_column(String(20))
    # The scope gate: in_scope | mixed | no_issue | out_of_scope (NULL: the gate didn't run, e.g. no customer)
    scope_decision: Mapped[Optional[str]] = mapped_column(String(20))
    scope_reason: Mapped[Optional[str]] = mapped_column(Text)
    customer_message: Mapped[Optional[str]] = mapped_column(Text)  # the message answered, shortened (quotes removed)
    escalate: Mapped[bool] = mapped_column(Boolean)
    escalation_note: Mapped[Optional[str]] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer)  # agent loop + reranker
    cached_input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    seconds: Mapped[float] = mapped_column(Float)       # retrieval + agent loop
    retrieval_seconds: Mapped[float] = mapped_column(Float, default=0.0)  # SOP search (hybrid + rerank)
    loop_seconds: Mapped[float] = mapped_column(Float, default=0.0)       # the tool-calling loop
    turns: Mapped[int] = mapped_column(Integer, default=0)                # LLM rounds in the loop
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
