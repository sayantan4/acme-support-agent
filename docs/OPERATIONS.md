# Operations: setup, running and internals

How to set up, run and operate the system, and how each part works inside. The overview, results and design
decisions are in the [README](../README.md).

New Gmail messages arrive through Gmail's `users.watch` → Cloud Pub/Sub (pull worker or push webhook) →
`history.list` sync.

```
Gmail ──watch──► Pub/Sub topic ──push (OIDC JWT)──► POST /pubsub/push ──► sync_mailbox()
                                                         │ 204 fast            │ history.list(cursor)
                                                         ▼                     ▼ messages.get
                                                    thread pool         message_service.on_new_message()
```

## Layout

The main app (`app/`) is layered; each layer only calls the ones below it:

```
controllers/   routes, CLI, workers: parse input, call a service, shape output
     │
services/      business rules; each service owns its transaction (session_scope)
     │                                   │
repositories/  all SQL, one per table    clients/  one wrapper per outside system
     │                                   │
models/        tables (SQLAlchemy)       Gmail API · Google OAuth · auth service · Redis Streams
     │
Postgres
```

| File | Role |
|---|---|
| `app/main.py` | `create_app()`: builds the Flask app and registers the HTTP controllers |
| `app/cli.py` | Entry point for `python -m app.cli <command>` (the commands live in `controllers/cli.py`) |
| **controllers/** | |
| `app/controllers/http_webhook.py` | Pub/Sub push endpoint: verify JWT, decode, ack, run the sync in a thread pool |
| `app/controllers/pubsub_worker.py` | Pull-mode worker: reads notifications from Pub/Sub, syncs, acks/nacks |
| `app/controllers/http_oauth.py` | `/oauth/start` → Google consent → `/oauth/callback` |
| `app/controllers/http_internal.py` | Internal API for ai_analyzer and ToolSvc (trusted hosts only) |
| `app/controllers/http_mailboxes.py` | `GET /mailboxes`, `GET /healthz` |
| `app/controllers/stream_workers.py` | Summary scheduler loop, and C2 (consumes `SUMMARY_COMPLETED`) |
| `app/controllers/cli.py` | `pull`, `summary-scheduler`, `summary-writer`, `renew-watches`, `sync-all`, `sync`, `stop`, `approvals`, `approve`, `reject` |
| **services/** | |
| `app/services/sync_service.py` | Cursor-based sync (Gmail history), dedupe, full resync on a stale cursor |
| `app/services/message_service.py` | **Your code goes here:** `on_new_message()` (auth check, mark for summary); reads for the internal API |
| `app/services/summary_service.py` | Summary batching, and C2's "keep the newest summary" rule |
| `app/services/reply_service.py` | Store replies from C3, hold escalated ones for approval, send through Gmail |
| `app/services/watch_service.py` | Gmail watch: start / renew (7-day lease) / disconnect |
| `app/services/mailbox_service.py` | Connect a Gmail account (OAuth → tokens → watch), list mailboxes |
| **clients/** | |
| `app/clients/gmail_client.py` | `GmailClient`: every Gmail API call; builds it for a mailbox and refreshes/persists its OAuth token |
| `app/clients/google_oauth_client.py` | Google's consent URL and code → tokens exchange |
| `app/clients/auth_client.py` | HTTP client for the auth service (`verified_user_id`) |
| `app/clients/stream_client.py` | Redis Streams publish / consume |
| **repositories/** | |
| `app/repositories/*_repo.py` | All SQL: `mailbox_repo`, `message_repo`, `summary_repo`. Take the caller's session, never commit |
| **models/** | |
| `app/models/` | One file per table (`mailbox.py`, `processed_message.py`, `thread_summary.py`); `session.py`: engine, `session_scope()`, `init_db()` |
| `app/utils/email_text.py` | Read a Gmail message (headers, body, automated?) and build a threaded reply. No I/O |
| **other services** | |
| `ai_analyzer/` | C1 support agent + C3 reply writer (`python -m ai_analyzer summarizer\|replier`), same layers: |
| `ai_analyzer/controllers/` | `summarize_worker.py` (C1) and `reply_worker.py` (C3): read a stream event, call a service |
| `ai_analyzer/services/` | `summarize_service.py` (C1: run the agent, publish summary + reply), `reply_service.py` (C3: stale check, store), `review_service.py` (run outcome → review) |
| `ai_analyzer/agent/` | The orchestrator (`orchestrator.py`): scope gate (`scope.py`) → main agent (retrieval → tool loop → summary + reply) or a template (`template_replies.py`); `evals/` (main agent) and `evals/gate_llm/` (gate) |
| `ai_analyzer/knowledge_retriever/` | SOP retrieval: BM25, dense, hybrid (RRF), LLM reranker; retrieval evals |
| `ai_analyzer/clients/` | `llm_client` (OpenAI), `main_app_client`, `toolsvc_client`, `stream_client` (Redis), `runs_file` |
| `ai_analyzer/models/`, `repositories/` | Its own Postgres tables (`agent_runs`, `llm_calls`, `reply_reviews`, `model_prices`, `eval_*`) and their queries |
| `ai_analyzer/tools/` | `tools.json`, the tool catalog the LLM is offered (no `user_id`); `export.py` regenerates it |
| `auth/` | **Auth service** (:8001, own DB): email -> `user_id`; `POST /register/user`, `POST /verify/user` |
| `toolsvc/` | **ToolSvc** (:8002): the 44 read-only support tools on a fake DB; injects the verified `user_id` per call |
| `scripts/setup_gcp.sh` | One-time topic + IAM + push subscription (+ dead-letter) setup |

## Two ways to receive notifications

| | **Pull** (default, first cut) | **Push** |
|---|---|---|
| How | `python -m app.cli pull` asks Pub/Sub for messages | Pub/Sub POSTs to `/pubsub/push` |
| Public URL / ngrok | Not needed | Needed (HTTPS) |
| Auth | Your gcloud credentials (ADC) | OIDC JWT verified in `webhook.py` |
| Failed sync | Message is nacked and redelivered | Next notification or `sync-all` catches up |

Both call the same `sync_mailbox()`. Only the way notifications arrive differs.

## Setup (pull mode, personal Gmail)

1. **Install** (Python 3.10+, [uv](https://docs.astral.sh/uv/), gcloud):
   ```bash
   uv sync
   cp .env.example .env
   ```
2. **GCP project under your personal Google account** (billing must be linked for Pub/Sub; usage here is free-tier):
   ```bash
   gcloud auth login                       # personal account
   gcloud auth application-default login   # lets the pull worker call Pub/Sub
   gcloud projects create <your-project-id>
   ```
3. **OAuth consent screen** (Console → Google Auth Platform / APIs & Services):
   - Audience: **External**, publishing status **Testing**
   - Add your personal Gmail under **Test users**
   - Data access: add scopes `https://www.googleapis.com/auth/gmail.readonly` and `https://www.googleapis.com/auth/gmail.send`
4. **OAuth client:** Credentials → Create OAuth client → *Web application*, redirect URI
   `http://localhost:8000/oauth/callback`. Download the JSON to `./client_secret.json`.
5. **Pub/Sub topic and pull subscription:**
   ```bash
   PROJECT_ID=<your-project-id> ./scripts/setup_gcp.sh
   ```
   Copy the printed `PUBSUB_TOPIC` and `PUBSUB_SUBSCRIPTION` into `.env`. Also set `TOKEN_ENCRYPTION_KEY`
   (the command to generate it is in `.env.example`).
6. **Connect your Gmail** (the web app is only needed for this OAuth step and runs on localhost only):
   ```bash
   uv run flask --app app.main:create_app run --port 8000
   open http://localhost:8000/oauth/start
   ```
   Google warns "Google hasn't verified this app". That's expected in Testing mode:
   **Advanced → Go to (app) → Allow**.
7. **Start the worker** in another terminal, then send yourself an email:
   ```bash
   uv run python -m app.cli pull
   ```
   The log shows `notification for ...`, then `new message id=... subject=...`.

**Testing-mode caveat:** for External apps in Testing, Google expires refresh tokens after **7 days**.
When the mailbox shows `disconnected`, visit `/oauth/start` again. Publishing the app (with Google's
verification for the restricted Gmail scope) removes this limit.

For **push mode** instead: `MODE=push PUSH_ENDPOINT=https://<host>/pubsub/push ./scripts/setup_gcp.sh`,
set the `PUBSUB_AUDIENCE` and `PUBSUB_SA_EMAIL` it prints, and run the Flask app on a public HTTPS URL.

## Thread summaries (AI pipeline)

```
handler: new inbound message → processed_messages.summary_status = 'pending'
                     │
scheduler, every 30 s (SUMMARY_BATCH_SECONDS): one request per thread with pending messages,
marks them 'requested'
                     │ XADD gmail:summary:requested {thread_id, summary_till_now, last_summary_msg_id}
                     ▼
ai_analyzer (C1): GET /internal/threads/<id>/messages?after=last_summary_msg_id
                  → orchestrator: scope gate, then main agent (SOPs + tools) or a template → summary + reply
          │ XADD gmail:summary:completed               │ XADD gmail:reply:ready
          │ {thread_id, summary_data,                   │ {in_reply_to_id, reply, last_summary_msg_id,
          │  last_summary_msg_id = newest msg covered}  │  escalate, sop_ids_used, tools_called}
          ▼                                             ▼
C2 (main app): saves it if newer         C3 (ai_analyzer replier): newer inbound message in the thread?
than the saved summary                   yes → "dropping", the next batch covers it; no → store the reply
```

- Message pointers are **our** `processed_messages.id` (auto-increment), not Gmail ids.
- **Batching:** messages aren't summarized one by one. M1 (22:00), M2 (22:02) and M3 (22:04) wait as
  `pending`; the next scheduler run sends one request for the thread and C1 summarizes all three in one
  LLM call. `summary_status` NULL means "not tracked" (outbound replies, and mail stored before batching).
- **scheduler** (`app/services/summary_service.py`, `python -m app.cli summary-scheduler`): marks and publishes in one
  transaction, so if Redis is down the messages stay `pending` for the next run. `--once` sends one batch
  and exits, for cron.
- **C1** (`ai_analyzer/services/summarize_service.py` + `ai_analyzer/agent/`): fetches every inbound message after
  `last_summary_msg_id` from the main app's internal API (localhost only), including ones that arrived
  after the request, and calls the orchestrator. Its scope gate (`SCOPE_MODEL`, default `gpt-5.4-nano`, prompt
  `scope-v3`) decides first: `no_issue` (thanks, greeting, noise) and `out_of_scope` get a template reply, or
  none, without running the agent; `in_scope` and `mixed` go to the main agent, which:
  1. finds up to 3 SOP sections graded 2+ by the reranker (prompt `rerank-v1`);
  2. offers the LLM only the tools on those sections' `TOOLS:` lines;
  3. runs the tool calls through ToolSvc with the message id of the email it answers (newest message from a
     verified customer that isn't automated mail), at most 8 rounds;
  4. gets back JSON: new summary, reply (or none), escalate + note, SOPs followed;
  5. prints one report block (SOPs, every tool call, final summary, final reply) and publishes the summary
     (C2) and the reply (C3). Model `OPENAI_MODEL`, default `gpt-5.4-mini`. It never imports `app/`.
- **C2** (`app/services/summary_service.py`): saves a summary only if it ends at a newer message than the saved one
  (`UPDATE … WHERE last_summary_msg_id < :new`). Older and duplicate results are skipped.

### Who the customer is (auth) and whose data tools see (ToolSvc)

```
new email ─► main app handler ─POST /verify/user─► auth: sender email -> user_id (strict: unknown or
             inactive sender = stored, never processed) -> processed_messages.user_id

ai_analyzer (LLM picks a tool) ─POST /tools/<name> {message_id, arguments}─► ToolSvc
             ToolSvc ─GET /internal/messages/<id>/user─► main app -> user_id -> runs the tool for that user only
```
- `message_id` comes from the stream event in our code, never from the LLM; `tools.json` has no `user_id`.
- Another customer's record ids answer "not found"; cross-account lookups only say "yours / not yours".
- Details: `auth/README.md`, `toolsvc/README.md`.

### Replies

- **C3** reads `gmail:reply:ready`, the reply the agent wrote. Before storing it checks the thread: if an
  inbound message newer than `last_summary_msg_id` exists, the reply may answer the wrong question and C3
  drops it (the next batch covers the newer message). Our own stored replies don't count.
- Replies go to `POST /internal/replies`; the main app stores a `processed_messages` row with
  `direction='outbound'`, `in_reply_to_id=<inbound id>`, `message_id='local-reply-<id>'`.
- Only **real people** get replies: `is_automated` is set at ingest from `List-Unsubscribe`, `Precedence`,
  `Auto-Submitted` and no-reply sender addresses.
- Only **verified customers'** messages leave the main app: the internal API leaves out messages from
  unknown senders, so a stranger in a thread never reaches the summary or the agent's prompt.
- Stored replies are **left out of summaries** (they were never sent).
- One reply per inbound message: the reply's `message_id` is derived from the inbound id.
- **Sending** needs `SEND_REPLIES=true`; the main app then sends the reply in the customer's Gmail thread. With it
  off (the default, and in tests) replies are only stored.
- **Escalated replies wait for a human** (`send_status = awaiting_approval`): `python -m app.cli approvals <mailbox>`
  lists them, `approve <reply_id>` re-publishes the reply so C3 sends it, `reject <reply_id>` means it is never sent.

Run (each in its own terminal):
```bash
redis-server --port 6380 --dir .redis-data --appendonly yes   # REDIS_URL=redis://localhost:6380/0
uv run flask --app app.main:create_app run --port 8000   # internal API for C1 and C3 (+ OAuth)
uv run python -m auth serve                  # auth service :8001 (first time: python -m auth seed)
uv run python -m toolsvc                     # ToolSvc :8002
uv run python -m app.cli pull                # Gmail -> handler (verifies sender, marks messages pending)
uv run python -m app.cli summary-scheduler   # every 30 s: pending messages -> C1
uv run python -m ai_analyzer summarizer      # C1: the agent (needs auth, ToolSvc, OpenAI)
uv run python -m app.cli summary-writer      # C2
uv run python -m ai_analyzer replier         # C3: stores the agent's replies
```
A failed event isn't acked; it stays pending in Redis and is retried when that consumer restarts.

### Or run everything in Docker (Colima or Docker Desktop)

```bash
docker compose up -d --build        # 13 containers: postgres, redis, web, auth, toolsvc, pull, scheduler,
                                    # C1 summarizer, C2 summary-writer, C3 replier, maintenance, prometheus, grafana
docker compose ps                   # status
docker compose logs -f summarizer   # one service's logs (C1); summary-writer = C2, replier = C3
docker compose run --rm auth python -m auth register you@gmail.com --user-id 101
open http://localhost:8000/oauth/start
```

| URL | |
|---|---|
| http://localhost:8000 | main app (OAuth at `/oauth/start`); auth :8001, toolsvc :8002 |
| http://localhost:3000 | Grafana, both dashboards (view without login; admin / admin to edit) |
| http://localhost:9090 | Prometheus (scrapes C1 at `summarizer:9101`) |
| `postgresql://acme:acme@localhost:5433` | the stack's Postgres (`gmail_webhooks`, `auth`, `ai_analyzer`) |

- One image (`Dockerfile`), one container per process. Settings come from `.env`; `docker-compose.yml`
  overrides only the hosts, ports and paths that differ in containers. Everything is published on 127.0.0.1 only.
- Needs `client_secret.json` in the repo root and gcloud ADC in `~/.config/gcloud` (override with
  `GCLOUD_CONFIG_DIR`); both are mounted read-only.
- Postgres, Redis, Prometheus and Grafana each have a named volume, so data survives `docker compose down`
  (not `down -v`). Move data in from another Postgres with `pg_dump --no-owner` +
  `docker compose exec -T postgres psql -U acme -d <db> < dump.sql` (see `db_dumps/README.md` in the archive).
- `maintenance` runs `sync-all` every 20 min (`SYNC_ALL_SECONDS`) and `renew-watches` daily.
- Containers reach each other's internal APIs through `TRUSTED_NETWORKS` / `AUTH_TRUSTED_NETWORKS` /
  `TOOLSVC_TRUSTED_NETWORKS` (the compose network, 172.28.0.0/16). Outside Docker these are empty: localhost only.
- Grafana here is 12.3 (its PostgreSQL datasource is built in); the datasource file reads hosts from the
  environment, so the same provisioning works in Docker and with `scripts/monitoring.sh` locally.
- Only one stack may consume the Gmail Pub/Sub subscription: don't run the local `app.cli pull` and the
  `pull` container at the same time.

## Production server

```bash
uv run gunicorn -w 1 --threads 8 -b 0.0.0.0:8000 'app.main:create_app()'
```
Keep `-w 1` (one process) until you've done the steps in "Scaling beyond one process" below:
the per-mailbox lock and the OAuth state store live in memory.

## Scheduled jobs

```bash
uv run python -m app.cli renew-watches   # daily: a watch silently stops after ~7 days
uv run python -m app.cli sync-all        # every 15–30 min: catches notifications that never arrived
```
Use cron, Cloud Scheduler → Cloud Run Job, or k8s CronJob.

## Behavior guarantees

- **At-least-once handling.** A message is marked processed only after `on_new_message` returns.
  If the handler raises, the cursor isn't advanced, and the next sync retries only the unprocessed messages.
- **Duplicate / out-of-order notifications are harmless.** Each one only triggers a sync from the
  stored cursor, and `processed_messages` dedupes by message ID.
- **Stale cursor (404 from `history.list`)** → full resync of the latest `FULL_SYNC_MAX_MESSAGES`
  inbox messages, then continue from the current `historyId`.
- **Revoked access** → the mailbox is marked `disconnected`. Visiting `/oauth/start` again reconnects it.

## Scaling beyond one process

This is the single-instance MVP. Before running multiple replicas:
- Replace the in-process per-mailbox lock in `app/services/sync_service.py` with Redis or a Postgres advisory lock.
- Replace the thread pool in `app/controllers/http_webhook.py` with a real queue (SQS / Cloud Tasks / Redis)
  that the workers consume.
- Move the OAuth `state` store in `app/services/mailbox_service.py` to Redis/DB.

## Monitoring (local Prometheus + Grafana)

```bash
brew install prometheus grafana        # once
scripts/monitoring.sh start            # Prometheus :9090, Grafana :3000; stop | status
scripts/monitoring.sh export           # after editing the dashboard in the UI: write it back to the JSON file
```
Open http://localhost:3000: the home dashboard is **AI agent: LLM calls, tokens, latency**. Everything is
code in `monitoring/`: `prometheus.yml` (scrape config), `grafana/provisioning/` (datasources),
`grafana/dashboards/ai_agent.json` (the dashboard). Viewing needs no login; sign in as **admin / admin**
(local only) to edit, then run `export` so the JSON file stays the source of truth.

| Source | What | Where it comes from |
|---|---|---|
| Postgres `ai_analyzer` | **Overall:** runs, LLM calls, tokens, cached %, approx cost (total, per run, per day, by purpose), exact p50/p95/p99 (whole run, retrieval, tool loop, each LLM purpose), outcomes, review verdicts, tool usage. **Selected run** (dropdown): overview with RIGHT / WRONG / NOT REVIEWED verdict, latency split, waterfall of its LLM calls, per-call tokens and cost, SOPs retrieved vs followed, tools, reply / escalation note / review notes | `agent_runs`, `llm_calls`, `reply_reviews`, `model_prices` |
| Prometheus | Aggregates: LLM-call latency p50/p95/p99 by purpose (agent, rerank, embedding, judge), retrieval and whole-run latency, token totals, cached share, tool calls by tool | C1's `/metrics` on `ANALYZER_METRICS_PORT` (9101) |

A second dashboard, **AI evals: retrieval and agent**, charts every eval run:

- how many cases per eval set (scored / out of scope), and every eval run so far;
- retrieval systems side by side (BM25, Dense, Hybrid, Hybrid + rerank-v1/v2): hit@1, hit@3, lenient hit@3, MRR,
  latency; hit@3 per eval set and per category; out-of-scope cut-offs; history across runs;
- a case explorer (pick a system): its misses, and the right SOP's rank in every system, case by case;
- the orchestrator: routing accuracy, real issues turned away, estimated cost and latency per message;
- the scope gate: accuracy, macro F1, precision and recall per class, a confusion matrix and its mistakes;
- the main agent: pass rate, missed escalations, judge scores, per-check pass rates per run, and the cases and judge
  rubric of a selected run.

The eval runners save JSON results files; `python -m ai_analyzer evals-import` loads them into the `eval_*` tables of
the `ai_analyzer` database (models in `ai_analyzer/models/evals/`). The runners import automatically after saving;
importing the same file twice changes nothing, and a re-run is kept as a new run.

Every LLM call goes through `ai_analyzer/clients/llm_client.py` (`respond`, `ask`, `embed`), which measures
it once: Prometheus via `clients/metrics_client.py`, and the agent run's own list (`result.llm_calls`).
Prometheus percentiles are bucket estimates; the Postgres panels give exact ones.

**Cost** is estimated in SQL: tokens × `model_prices`, loaded from `ai_analyzer/model_prices.json` when C1 starts
(or `python -m ai_analyzer prices`). Prices are OpenAI's Standard tier, checked 2026-10-14; when they change, edit the
JSON: all history is re-priced because cost is computed when the dashboard is viewed.

## Tests

```bash
uv run pytest
```
Tests run against Postgres: each run creates throwaway databases (`test_<id>_main`, `_auth`,
`_analyzer`) and drops them at the end. By default they use the docker compose Postgres
(`docker compose up -d postgres`, localhost:5433); for another server set `TEST_POSTGRES_URL`, e.g.
`postgresql+psycopg://localhost:5432/postgres`.


## Eval sets

| File | Cases | Source | Tests |
|---|---|---|---|
| `ai_analyzer/knowledge_retriever/evals/evals.json` | 78 | Hand-labelled | Retrieval, routing, reply checks |
| `ai_analyzer/knowledge_retriever/evals/evals_long.json` | 13 | Hand-written long emails | Retrieval |
| `ai_analyzer/knowledge_retriever/evals/evals_banking77.json` | 116 | [Banking77](https://github.com/PolyAI-LDN/task-specific-datasets) test split (PolyAI, CC BY 4.0), queries unchanged; built by `scripts/import_banking77.py` | Retrieval only, incl. 20 out-of-scope (cards/ATM/FX) |
| `ai_analyzer/knowledge_retriever/evals/evals_cfpb.json` | 351 | Real CFPB complaint narratives, localised to India and Acme Invest | Retrieval, incl. 116 out-of-scope |
| `ai_analyzer/agent/evals/gate_llm/scope_cases.json` | 41 | Hand-written threads | Scope gate (also runs on the 4 sets above, with `scope_label_overrides.json`) |
| `ai_analyzer/agent/evals/thread_cases.json` | 4 | Real threads (customer wrote, we replied, customer wrote again) | Main agent |

Check them with `python scripts/check_evals.py [file]`. Banking77 attribution: Casanueva et al., 2020,
"Efficient Intent Detection with Dual Sentence Encoders". Its intent-to-SOP mapping (`INTENT_TO_SOP`
in the import script) needs human review.
