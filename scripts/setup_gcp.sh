#!/usr/bin/env bash
# One-time GCP setup: Pub/Sub topic Gmail can publish to + a subscription for this app.
#
# Pull mode (default, no public URL needed):
#   PROJECT_ID=my-proj ./scripts/setup_gcp.sh
# Push mode (Pub/Sub POSTs to your HTTPS endpoint):
#   MODE=push PROJECT_ID=my-proj PUSH_ENDPOINT=https://your.domain.com/pubsub/push ./scripts/setup_gcp.sh
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
MODE="${MODE:-pull}"
TOPIC="${TOPIC:-gmail-inbox}"
DLQ_TOPIC="${DLQ_TOPIC:-gmail-inbox-dlq}"

gcloud config set project "$PROJECT_ID"
gcloud services enable gmail.googleapis.com pubsub.googleapis.com

# Topic + permission for Gmail's system account to publish to it
gcloud pubsub topics create "$TOPIC" || true
gcloud pubsub topics add-iam-policy-binding "$TOPIC" \
  --member="serviceAccount:gmail-api-push@system.gserviceaccount.com" \
  --role="roles/pubsub.publisher"

# Dead-letter topic for messages that keep failing
gcloud pubsub topics create "$DLQ_TOPIC" || true

COMMON_FLAGS=(
  --topic="$TOPIC"
  --ack-deadline=60
  --min-retry-delay=10s --max-retry-delay=600s
  --dead-letter-topic="$DLQ_TOPIC" --max-delivery-attempts=10
)

if [[ "$MODE" == "pull" ]]; then
  SUBSCRIPTION="${SUBSCRIPTION:-gmail-inbox-pull}"
  gcloud pubsub subscriptions create "$SUBSCRIPTION" "${COMMON_FLAGS[@]}" || true
  ENV_LINES="  PUBSUB_TOPIC=projects/${PROJECT_ID}/topics/${TOPIC}
  PUBSUB_SUBSCRIPTION=projects/${PROJECT_ID}/subscriptions/${SUBSCRIPTION}"
elif [[ "$MODE" == "push" ]]; then
  : "${PUSH_ENDPOINT:?set PUSH_ENDPOINT (public HTTPS URL ending in /pubsub/push)}"
  SUBSCRIPTION="${SUBSCRIPTION:-gmail-inbox-push}"
  PUSH_SA_NAME="${PUSH_SA_NAME:-gmail-pusher}"
  PUSH_SA="${PUSH_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
  # Service account whose OIDC token Pub/Sub attaches to each push
  gcloud iam service-accounts create "$PUSH_SA_NAME" --display-name="Gmail Pub/Sub pusher" || true
  gcloud pubsub subscriptions create "$SUBSCRIPTION" "${COMMON_FLAGS[@]}" \
    --push-endpoint="$PUSH_ENDPOINT" \
    --push-auth-service-account="$PUSH_SA" \
    --push-auth-token-audience="$PUSH_ENDPOINT" || true
  ENV_LINES="  PUBSUB_TOPIC=projects/${PROJECT_ID}/topics/${TOPIC}
  PUBSUB_AUDIENCE=${PUSH_ENDPOINT}
  PUBSUB_SA_EMAIL=${PUSH_SA}"
else
  echo "MODE must be pull or push" >&2
  exit 1
fi

# Pub/Sub's service agent needs rights to forward to the DLQ and ack the original
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
PUBSUB_AGENT="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"
gcloud pubsub topics add-iam-policy-binding "$DLQ_TOPIC" --member="$PUBSUB_AGENT" --role="roles/pubsub.publisher"
gcloud pubsub subscriptions add-iam-policy-binding "$SUBSCRIPTION" --member="$PUBSUB_AGENT" --role="roles/pubsub.subscriber"

cat <<EOF

Done (${MODE} mode). Put these in .env:
${ENV_LINES}
EOF
