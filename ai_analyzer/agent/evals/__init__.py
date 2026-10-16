"""Agent evals: does the support agent do the right thing for a given customer email?

    uv run python -m ai_analyzer.agent.evals.run_evals                  # all cases
    uv run python -m ai_analyzer.agent.evals.run_evals --cases a,b      # some cases
    uv run python -m ai_analyzer.agent.evals.run_evals --no-judge       # deterministic checks only

Files:
    cases.json    the eval emails, each with what the agent should do (from the SOPs and the fake data)
    checks.py     deterministic checks: SOP, tools, escalation, reply or not, no internal codes
    judge.py      LLM judge: reply covers the must-say points, avoids the must-not points, is grounded
    run_evals.py  runs the real agent on every case (tools in-process on ToolSvc's fake DB), scores, saves
    gate_llm/     evals for the scope gate (the orchestrator's first step); see gate_llm/__init__.py

Each case runs the real retrieval and the real LLM; only the tool transport is different (in-process
instead of HTTP), so no services need to be running.
"""
