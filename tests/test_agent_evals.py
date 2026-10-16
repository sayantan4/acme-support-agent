"""The agent-eval scoring itself (checks, judge parsing, summary), with fake agent results: no LLM."""

from types import SimpleNamespace

from ai_analyzer.agent.evals import run_evals
from ai_analyzer.agent.evals.checks import run_checks
from ai_analyzer.agent.evals.judge import judge_reply
from ai_analyzer.agent.loop import AgentResult, ToolCallRecord
from ai_analyzer.clients.llm_client import LLMResult
from toolsvc import fake_db


def agent_result(reply="Hi, your order is partly filled: 0.0008 BTC bought.", escalate=False,
                 tools=("check_order",), sops=("TRD-03",), followed=("TRD-03",)):
    return AgentResult(
        summary="s", reply=reply, escalate=escalate, escalation_note=None, sop_ids_used=list(followed),
        reply_to_message_id=1,
        sops=[SimpleNamespace(section=SimpleNamespace(sop_id=sop_id), grade=3) for sop_id in sops],
        tool_calls=[ToolCallRecord(turn=1, name=name, arguments={}, ok=True, result_preview="", output={"result": {}})
                    for name in tools],
    )


EXPECT = {"sop_ids": ["TRD-03"], "tools_called": ["check_order"], "tools_not_called": ["find_user"],
          "escalate": False, "reply": True, "must_contain_any": ["0.0008"]}


def failed(expect, result):
    return {c.name: c.detail for c in run_checks(expect, result) if not c.passed}


def test_a_correct_run_passes_every_check():
    assert failed(EXPECT, agent_result()) == {}


def test_each_check_catches_its_own_mistake():
    assert "sop" in failed(EXPECT, agent_result(sops=("TRD-01",), followed=("TRD-01",)))
    assert "sop" in failed(EXPECT, agent_result(followed=()))  # retrieved but not followed
    assert failed(EXPECT, agent_result(tools=()))["tools_called"] == "missing ['check_order']"
    assert "tools_not_called" in failed(EXPECT, agent_result(tools=("check_order", "find_user")))
    assert failed(EXPECT, agent_result(reply="Hi, your order is fine."))["must_contain_any"]


def test_missed_escalation_is_named_as_such():
    assert failed({**EXPECT, "escalate": True}, agent_result())["escalation"] == "MISSED escalation"
    assert failed(EXPECT, agent_result(escalate=True))["escalation"] == "unneeded escalation"


def test_reply_decision_and_internal_terms():
    assert failed({**EXPECT, "reply": False, "must_contain_any": []}, agent_result())["reply_decision"] == \
        "replied when no reply was needed"
    leak = failed(EXPECT, agent_result(reply="Rejected: PRICE_OUT_OF_BAND (check_order says 0.0008)"))
    assert "PRICE_OUT_OF_BAND" in leak["no_internal_terms"] and "check_order" in leak["no_internal_terms"]


def test_out_of_scope_case_needs_no_sop():
    assert "sop" not in failed({**EXPECT, "sop_ids": [], "tools_called": [], "must_contain_any": []},
                               agent_result(sops=(), followed=(), tools=()))


def test_judge_verdict_is_parsed_and_failures_counted():
    case = {"messages": [{"from": "a", "subject": "s", "body": "b"}],
            "expect": {"reply_must": ["says partly filled"], "reply_must_not": ["promises follow-up"]}}
    reply = ('```json\n{"items": [{"item": "says partly filled", "kind": "must", "passed": true, "reason": "ok"},'
             '{"item": "promises follow-up", "kind": "must_not", "passed": false, "reason": "it does"}],'
             '"grounded": true, "grounded_reason": ""}\n```')
    verdict = judge_reply(case, agent_result(), ask_llm=lambda i, u: LLMResult(reply, "fake", 10, 5))
    assert verdict.error is None and verdict.grounded and verdict.passed is False
    assert [item["passed"] for item in verdict.items] == [True, False]

    broken = judge_reply(case, agent_result(), ask_llm=lambda i, u: LLMResult("not json", "fake", 1, 1))
    assert broken.error and broken.passed is False


def test_run_and_summarize_with_a_fake_agent(monkeypatch):
    fake_db.reset()
    monkeypatch.setattr(run_evals, "run_agent", lambda summary, messages, reply_to_message_id, **kwargs: agent_result())
    case = {"id": "c1", "user_id": 101, "messages": [{"from": "a", "subject": "s", "body": "b"}],
            "expect": {**EXPECT, "reply_must": [], "reply_must_not": []}}
    result = run_evals.evaluate_case(case, use_judge=False)
    assert result["passed"] is True and result["agent"]["tools_called"][0]["name"] == "check_order"

    summary = run_evals.summarize([result, {**result, "passed": False}])
    assert (summary["cases"], summary["passed"], summary["pass_rate"]) == (2, 1, 0.5)
    assert summary["check_pass_rates"]["sop"] == 1.0


def test_in_process_tools_use_the_cases_customer():
    fake_db.reset()
    run_tool = run_evals.tools_for(102)
    assert run_tool("check_deposit", {"deposit_id": 403}, message_id=1)["result"]["deposit"]["user_id"] == 102
    assert run_tool("check_deposit", {"deposit_id": 401}, message_id=1) == {"error": "deposit 401 not found"}


def test_not_a_repeat_only_runs_on_thread_cases_and_catches_a_resent_reply():
    holding = "Thank you for writing to us. Your message has been passed to a senior member of our team."
    expect = {**EXPECT, "must_contain_any": []}
    assert "not_a_repeat" not in {c.name for c in run_checks(expect, agent_result(reply=holding))}
    repeat = {c.name: c for c in run_checks(expect, agent_result(reply=holding + " "), [holding])}["not_a_repeat"]
    assert repeat.passed is False and "1.00" in repeat.detail
    fresh = {c.name: c for c in run_checks(expect, agent_result(), [holding])}["not_a_repeat"]
    assert fresh.passed is True


def test_thread_cases_file_is_well_formed():
    cases = run_evals.load_cases(suite="thread")
    assert len(cases) >= 3 and len({c["id"] for c in cases}) == len(cases)
    for case in cases:
        assert case["previous_replies"] and case["messages"] and "escalate" in case["expect"]


def test_max_words_only_when_the_case_sets_it():
    closing = {**EXPECT, "must_contain_any": [], "max_words": 12}
    assert "max_words" not in failed(closing, agent_result(reply="Glad I could help. Have a nice day! Acme Invest Support"))
    assert failed(closing, agent_result(reply="word " * 13))["max_words"] == "13 words (limit 12)"
    assert "max_words" not in {c.name for c in run_checks(EXPECT, agent_result())}
