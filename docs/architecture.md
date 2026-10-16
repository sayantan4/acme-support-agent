# Architecture

## The whole system

Every box is one process in `docker-compose.yml`, and each service owns its own Postgres database.
Numbers follow one customer email from arrival to reply.

```mermaid
flowchart TB
    customer(["Customer"])
    gmail["Gmail + Pub/Sub"]

    subgraph main["Main app (app/)"]
        direction LR
        pull["pull<br/>stores the email"]
        scheduler["summary-scheduler<br/>one request per thread, every 30 s"]
        web["web :8000<br/>internal API, sends replies"]
        c2["C2 summary-writer"]
        db1[("gmail_webhooks DB")]
    end

    subgraph streams["Redis Streams"]
        direction LR
        s1[["summary:requested"]]
        s2[["summary:completed"]]
        s3[["reply:ready"]]
    end

    subgraph ai["AI analyzer (ai_analyzer/)"]
        direction LR
        c1["C1 summarizer"]
        orch{{"Orchestrator<br/>scope gate, then main agent or template"}}
        c3["C3 replier"]
        db2[("ai_analyzer DB<br/>runs, LLM calls, reviews, evals")]
    end

    auth["Auth :8001<br/>is the sender a customer?"]
    toolsvc["ToolSvc :8002<br/>read-only account tools"]
    openai(["OpenAI"])
    grafana["Prometheus + Grafana"]

    customer -- "1 email" --> gmail
    gmail -- "2 notification" --> pull
    pull -. "verify sender" .-> auth
    scheduler -- "3" --> s1
    s1 -- "4" --> c1
    c1 --> orch
    orch -.-> openai
    orch -. "tool calls" .-> toolsvc
    c1 -- "5 run + PENDING review" --> db2
    c1 -- "6 summary" --> s2
    c1 -- "6 reply" --> s3
    s2 --> c2
    s3 -- "7" --> c3
    c3 -- "8 store, drop if stale" --> web
    web -- "9 send in thread<br/>(escalated: waits for a human)" --> gmail
    gmail -- "10 reply" --> customer
    db2 -.-> grafana
```

## Inside the orchestrator

What C1's one call, `orchestrator.handle()`, does with a thread's new messages.

```mermaid
flowchart TD
    start(["New messages from a verified customer"])
    gate{"Scope gate<br/>gpt-5.4-nano, prompt scope-v3"}

    start --> gate
    gate -- "in_scope" --> agent
    gate -- "mixed<br/>(+ note: decline the other part)" --> agent
    gate -- "no_issue<br/>thanks, greeting, noise" --> tmpl
    gate -- "out_of_scope" --> tmpl
    gate -. "gate fails: fail open" .-> agent

    subgraph agent["Main agent (gpt-5.4-mini, prompt agent-v3)"]
        direction TB
        retrieve["Find SOPs<br/>BM25 + dense, RRF, LLM reranker"]
        tools["Tool loop<br/>only the SOPs' tools, via ToolSvc"]
        answer["Structured answer<br/>summary, reply, reply_kind, escalate"]
        retrieve --> tools --> answer
    end

    tmpl["Template reply<br/>sent at most once in a row;<br/>silent if a human owns the case"]

    answer --> result
    tmpl --> result
    result(["One result to C1:<br/>summary + reply (or none) + every LLM call"])
```
