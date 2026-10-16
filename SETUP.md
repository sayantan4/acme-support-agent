# Setup: from `git clone` to a reply in your inbox

This guide starts from a fresh clone. It takes about 30 minutes, most of it in the Google Cloud console.
You end up with two Gmail accounts talking to each other: a **support inbox** the agent reads and replies from,
and a **customer address** you write from.

## What you need

| | Why |
|---|---|
| Docker with Compose v2 and buildx ([Docker Desktop](https://www.docker.com/products/docker-desktop/), or [Colima](https://github.com/abiosoft/colima) + `brew install docker docker-compose docker-buildx`) | Runs the 13 services |
| [gcloud CLI](https://cloud.google.com/sdk/docs/install) and a Google account with billing enabled | Gmail API + Pub/Sub. Usage here stays in the free tier, but Pub/Sub needs a billing account linked |
| A Gmail address for the **support inbox** | The mailbox the agent reads and replies from |
| A second email address for the **customer** | Where you send test emails from |
| An [OpenAI API key](https://platform.openai.com/api-keys) | gpt-5.4-mini, gpt-5.4-nano and text-embedding-3-small. A typical email costs well under $0.01 |
| [uv](https://docs.astral.sh/uv/) (optional) | Only to run the tests and evals outside Docker |

## 1. Clone and create `.env`

```bash
git clone <this repo> acme-support && cd acme-support
cp .env.example .env
```

Generate the key that encrypts the stored Gmail token, and put it in `.env` as `TOKEN_ENCRYPTION_KEY`:

```bash
openssl rand -base64 32 | tr '+/' '-_'
```

Also set `OPENAI_API_KEY` in `.env` now.

## 2. Google Cloud: project, APIs, Pub/Sub

```bash
gcloud auth login                         # the Google account that owns the project
gcloud projects create <project-id>       # or reuse an existing project
# link billing in the console: Billing → link a billing account to <project-id>
gcloud auth application-default login     # credentials the pull worker uses for Pub/Sub
PROJECT_ID=<project-id> ./scripts/setup_gcp.sh
```

`setup_gcp.sh` enables the Gmail and Pub/Sub APIs, creates the `gmail-inbox` topic (Gmail is allowed to
publish to it), a dead-letter topic, and the `gmail-inbox-pull` subscription. Copy the two lines it prints into
`.env`:

```
PUBSUB_TOPIC=projects/<project-id>/topics/gmail-inbox
PUBSUB_SUBSCRIPTION=projects/<project-id>/subscriptions/gmail-inbox-pull
```

Docker mounts your gcloud credentials read-only from `~/.config/gcloud` (set `GCLOUD_CONFIG_DIR` in your shell
if they live elsewhere).

## 3. Google OAuth: let the app read and send mail

In the [Google Cloud console](https://console.cloud.google.com/), for the same project:

1. **Google Auth Platform → Branding / Audience:** create the consent screen. Audience **External**, status
   **Testing**. Under **Test users**, add the support inbox address.
2. **Data access:** add the scopes `https://www.googleapis.com/auth/gmail.readonly` and
   `https://www.googleapis.com/auth/gmail.send`.
3. **Clients → Create client:** type **Web application**, authorized redirect URI
   `http://localhost:8000/oauth/callback`. Download the JSON and save it as **`client_secret.json`** in the repo
   root (it is git-ignored).

Then, in `.env`:

```
GMAIL_SCOPES=https://www.googleapis.com/auth/gmail.readonly,https://www.googleapis.com/auth/gmail.send
SEND_REPLIES=true      # false (the default): replies are stored, never sent
```

## 4. Register yourself as a customer

Only registered customers get answers, and ToolSvc has demo account data (deposits, orders, KYC…) for customer
ids **101–106**. Link your customer address to 101 **before** the first full start; on start the auth service
seeds the demo customers and keeps any id that is already taken:

```bash
docker compose up -d postgres
docker compose run --rm auth python -m auth register customer@example.com --user-id 101
```

Already started everything? Point 101 at your address instead:
`docker compose exec postgres psql -U acme -d auth -c "UPDATE users SET email_id = 'customer@example.com' WHERE user_id = 101"`.

## 5. Start everything

```bash
docker compose up -d --build
docker compose ps          # 13 services, all "Up" (postgres and redis "healthy")
```

The first build takes a few minutes. When `summarizer` (C1) starts it embeds the SOP sections once (a few cents)
and loads the model prices.

## 6. Connect the support inbox

Open **http://localhost:8000/oauth/start** and sign in as the **support inbox** account. Google warns "Google
hasn't verified this app": that is expected in Testing mode, so choose **Advanced → Go to (app) → Allow**. The app
stores the token (encrypted) and starts a Gmail watch.

Check it:

```bash
curl -s localhost:8000/mailboxes      # status "active" and a watch_expiration about 7 days out
```

## 7. Send a test email

From the customer address, email the support inbox, for example:

| Email | What should happen |
|---|---|
| "hi, how are you?" | One small-talk template reply; no agent run |
| "do you offer personal loans?" | The out-of-scope template |
| "Where is my INR deposit? I paid by UPI yesterday" | The main agent follows the deposit SOP, checks customer 101's deposits and answers |

A reply arrives within about a minute (messages are batched every 30 seconds). Follow it in the logs:

```bash
docker compose logs -f pull summarizer replier
```

**Escalations wait for you.** When the agent escalates (a legal threat, a frozen account…), the reply is held:

```bash
docker compose exec web python -m app.cli approvals support@example.com   # the support inbox address
docker compose exec web python -m app.cli approve <reply_id>              # or: reject <reply_id>
```

## 8. Look inside

| URL | |
|---|---|
| http://localhost:3000 | Grafana: **AI agent** (pick a run: waterfall, latency and cost per stage, every LLM call) and **AI evals**. No login to view; admin / admin to edit |
| http://localhost:9090 | Prometheus |
| http://localhost:8000/mailboxes | Connected mailboxes |
| `postgresql://acme:acme@localhost:5433` | The stack's Postgres: `gmail_webhooks`, `auth`, `ai_analyzer` |

## 9. Tests and evals (optional)

```bash
uv sync
uv run pytest                 # about 200 tests; uses the stack's Postgres on :5433, never calls OpenAI or Gmail
```

The evals call OpenAI for real, so they cost money:

```bash
uv run python -m ai_analyzer.agent.evals.gate_llm.run_scope_evals --retrieval-sets   # scope gate, ~$0.25
uv run python -m ai_analyzer.agent.evals.run_evals --suite thread                    # main agent, 4 cases
```

Each run is imported into Postgres and shows up on the AI evals dashboard. See
[`docs/OPERATIONS.md`](docs/OPERATIONS.md) for the retrieval evals and everything else.

## Day to day

```bash
docker compose stop            # stop; data is kept in named volumes
docker compose start           # start again
docker compose down -v         # remove everything, including the databases
```

## When something goes wrong

| Symptom | Cause and fix |
|---|---|
| Mailbox shows `disconnected`, or no notifications after about a week | In Testing mode Google expires the refresh token after 7 days. Open http://localhost:8000/oauth/start again |
| `403 insufficient authentication scopes` when sending | The token was granted before `gmail.send` was added. Check `GMAIL_SCOPES` in `.env`, then reconnect at `/oauth/start` |
| Emails arrive but nothing is answered | The sender isn't registered (step 4), or the mail looks automated (newsletters and auto-replies are never answered) |
| A reply was stored but not sent | `SEND_REPLIES` is false, or the reply was escalated and waits for `approve` |
| `pull` logs permission errors | Run `gcloud auth application-default login` again, then `docker compose restart pull` |
| Two machines fight over messages | Only one running stack may read the Pub/Sub subscription. Stop the other one |
