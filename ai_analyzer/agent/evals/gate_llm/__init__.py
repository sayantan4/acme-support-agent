"""Gate evals: does the scope gate (agent/scope.py) route each message the right way?

    uv run python -m ai_analyzer.agent.evals.gate_llm.run_scope_evals                    # handwritten cases
    uv run python -m ai_analyzer.agent.evals.gate_llm.run_scope_evals --retrieval-sets   # + the retrieval emails

Files:
    scope_cases.json            handwritten threads: small talk, thanks, noise, out of scope, mixed, tricky in-scope
    scope_label_overrides.json  hand-reviewed scope labels for the retrieval eval emails
    run_scope_evals.py          runs the real gate on every case, scores it, saves results/
"""
