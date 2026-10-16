"""The scope gate's parsing and fail-open rule, the template replies and their guards. No LLM."""

import json
from types import SimpleNamespace

from ai_analyzer.agent.evals.gate_llm import run_scope_evals
from ai_analyzer.agent.scope import ScopeDecision, check_scope
from ai_analyzer.agent.template_replies import reply_without_agent


def gate_says(**answer):
    answer = {"decision": "in_scope", "no_issue_kind": None, "out_of_scope_topics": [], "reason": "r",
              "summary": "s", **answer}
    return lambda purpose, **request: SimpleNamespace(output_text=json.dumps(answer))


def decide(**answer):
    return check_scope("", [{"body": "hi"}], [], None, create=gate_says(**answer))


def test_the_gate_answer_is_read_and_unusable_answers_go_to_the_agent():
    assert decide(decision="no_issue", no_issue_kind="greeting").no_issue_kind == "greeting"
    assert decide(decision="out_of_scope", out_of_scope_topics=["loans"]).out_of_scope_topics == ["loans"]
    assert decide(decision="no_issue", no_issue_kind=None).decision == "in_scope"  # no_issue without a kind
    assert decide(decision="in_scope", out_of_scope_topics=["x"]).out_of_scope_topics == []


def test_when_the_gate_fails_the_message_goes_to_the_agent():
    def broken(purpose, **request):
        raise TimeoutError("nano is down")
    decision = check_scope("summary", [{"body": "hi"}], [], None, create=broken)
    assert (decision.decision, decision.failed, decision.summary) == ("in_scope", True, "summary")


def template(decision, no_issue_kind=None, topics=(), previous=(), last_kind=None):
    gate = ScopeDecision(decision=decision, reason="r", summary="s", no_issue_kind=no_issue_kind,
                         out_of_scope_topics=list(topics))
    return reply_without_agent(gate, list(previous), last_kind, reply_to_message_id=7)


def test_small_talk_gets_one_steer_then_silence():
    first = template("no_issue", "greeting")
    assert first.reply_kind == "small_talk" and "How can I help" in first.reply
    second = template("no_issue", "greeting", previous=[first.reply], last_kind="small_talk")
    assert second.reply is None and "already small talk" in second.reply_dropped


def test_thanks_on_an_open_case_never_says_glad_i_could_help():
    open_case = template("no_issue", "thanks_open")
    assert "patience" in open_case.reply and "Glad I could help" not in open_case.reply
    assert template("no_issue", "thanks_solved", last_kind="closing").reply is None  # closing after closing


def test_no_automatic_reply_when_a_human_owns_the_case_or_nothing_was_said():
    assert template("no_issue", "thanks_open", last_kind="holding").reply_dropped.startswith("a human owns")
    assert template("no_issue", "noise").reply is None


def test_out_of_scope_names_the_topics_safely_and_is_not_sent_twice():
    reply = template("out_of_scope", topics=["credit cards", "loans <a href=x>"]).reply
    assert "doesn't offer credit cards or loans a href x" in reply and "<" not in reply
    assert "loan credit products" in template("out_of_scope", topics=["loan/credit products"]).reply
    again = template("out_of_scope", topics=["loans"], previous=[reply],
                     last_kind="out_of_scope")
    assert again.reply is None and "already out of scope" in again.reply_dropped


def test_scope_cases_file_is_well_formed():
    cases = run_scope_evals.load_cases()
    assert len(cases) >= 40 and len({c["id"] for c in cases}) == len(cases)
    assert {c["expect"]["decision"] for c in cases} == {"in_scope", "mixed", "no_issue", "out_of_scope"}


def test_rescore_grades_saved_results_against_the_current_labels_without_api_calls(tmp_path, monkeypatch):
    case = {"id": "x", "category": "set:evals", "messages": [{"body": "Do you offer car loans?"}],
            "expect": {"decision": "out_of_scope"}}
    monkeypatch.setattr(run_scope_evals, "load_cases", lambda with_retrieval_sets=False: [case])
    saved = {"scope_prompt": "scope-v1", "summary": {}, "cases": [
        {"id": "x", "category": "set:evals", "expected": "in_scope", "decision": "out_of_scope", "passed": False,
         "problems": ["decision out_of_scope, expected in_scope"], "no_issue_kind": None, "reply_sent": True,
         "reason": "", "gate_failed": False, "wrongly_rejected": True, "seconds": 1.0,
         "tokens": {"input": 10, "cached": 0, "output": 5}},
        {"id": "dropped-as-ambiguous", "category": "set:evals", "expected": "in_scope", "decision": "in_scope",
         "passed": True, "problems": [], "no_issue_kind": None, "reply_sent": None, "reason": "",
         "gate_failed": False, "wrongly_rejected": False, "seconds": 1.0, "tokens": {"input": 10, "cached": 0, "output": 5}},
    ]}
    path = tmp_path / "eval_results_scope-v1__scope.json"
    path.write_text(json.dumps(saved))

    run_scope_evals.rescore(path)

    rescored = json.loads(path.read_text())
    [result] = rescored["cases"]
    assert (result["expected"], result["passed"], result["wrongly_rejected"]) == ("out_of_scope", True, False)
    assert result["message"] == "Do you offer car loans?"
    assert rescored["summary"]["wrongly_rejected"] == 0 and rescored["rescored_with_current_labels"] is True


def test_structured_output_models_produce_the_schema_openai_strict_mode_expects():
    # The SDK's own Pydantic -> strict schema converter (private API, so only used in this test). A field
    # with a default, or an object that allows extra keys, would make the two differ and OpenAI reject it.
    from openai.lib._pydantic import to_strict_json_schema

    from ai_analyzer.agent.prompt import AgentAnswer
    from ai_analyzer.agent.scope import ScopeAnswer
    for answer_model in (ScopeAnswer, AgentAnswer):
        assert answer_model.model_json_schema() == to_strict_json_schema(answer_model)


def test_a_gate_answer_of_the_wrong_shape_fails_open():
    decision = check_scope("summary", [{"body": "hi"}], [], None, create=gate_says(decision="maybe"))
    assert (decision.decision, decision.failed) == ("in_scope", True)
