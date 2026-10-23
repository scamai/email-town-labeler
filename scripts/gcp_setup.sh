#!/usr/bin/env bash
# Prepare a Google Cloud project for email-town-labeler.
#
# Automates what gcloud can do (create project, enable the Gmail API) and prints the exact
# console URLs for the steps Google only offers in the web console (OAuth consent screen,
# Desktop OAuth client). Safe to re-run: every step checks before it changes anything.
#
# Usage:
#   scripts/gcp_setup.sh [--project PROJECT_ID] [--dry-run]
#
# Requires the Google Cloud CLI (https://cloud.google.com/sdk/docs/install) and
# `gcloud auth login` with the Google account that should own the project.
# Full guide: docs/google-cloud-setup.md

set -euo pipefail

PROJECT_ID="${EMAIL_TOWN_LABELER_GCP_PROJECT:-}"
DRY_RUN=0

usage() {
  sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    -p|--project) PROJECT_ID="${2:?--project needs a value}"; shift 2 ;;
    --project=*)  PROJECT_ID="${1#*=}"; shift ;;
    -n|--dry-run) DRY_RUN=1; shift ;;
    -h|--help)    usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

run() {
  # Print, then execute unless --dry-run.
  printf '+ %s\n' "$*"
  if [ "$DRY_RUN" -eq 0 ]; then "$@"; fi
}

if ! command -v gcloud >/dev/null 2>&1; then
  echo "error: gcloud not found. Install it from https://cloud.google.com/sdk/docs/install" >&2
  echo "       or follow the manual steps in docs/google-cloud-setup.md." >&2
  exit 1
fi

ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -n1 || true)"
if [ -z "$ACCOUNT" ]; then
  if [ "$DRY_RUN" -eq 1 ]; then
    ACCOUNT="you@example.com"
    echo "(dry run) no active gcloud account; pretending to be $ACCOUNT"
  else
    echo "error: no active gcloud account. Run: gcloud auth login" >&2
    exit 1
  fi
fi

if [ -z "$PROJECT_ID" ]; then
  # Deterministic per account so re-running finds the same project instead of making a new one.
  # Project ids are global: 6-30 chars, lowercase letters, digits, hyphens.
  if command -v sha256sum >/dev/null 2>&1; then
    SUFFIX="$(printf '%s' "$ACCOUNT" | sha256sum | cut -c1-8)"
  else
    SUFFIX="$(printf '%s' "$ACCOUNT" | shasum -a 256 | cut -c1-8)"
  fi
  PROJECT_ID="email-town-labeler-${SUFFIX}"
fi

if ! printf '%s' "$PROJECT_ID" | grep -Eq '^[a-z][a-z0-9-]{4,28}[a-z0-9]$'; then
  echo "error: '$PROJECT_ID' is not a valid project id (6-30 chars: a-z, 0-9, '-', start with a letter)" >&2
  exit 2
fi

echo "account: $ACCOUNT"
echo "project: $PROJECT_ID"
echo

# 1. Project
if gcloud projects describe "$PROJECT_ID" --format='value(projectId)' >/dev/null 2>&1; then
  echo "[ok] project $PROJECT_ID exists"
else
  echo "[..] creating project $PROJECT_ID"
  run gcloud projects create "$PROJECT_ID" --name="email-town-labeler"
fi

# 2. Gmail API (enabling an enabled service is a no-op, but check to keep output quiet)
if [ "$DRY_RUN" -eq 0 ] && gcloud services list --enabled --project "$PROJECT_ID" \
     --filter='config.name=gmail.googleapis.com' --format='value(config.name)' 2>/dev/null \
     | grep -q gmail.googleapis.com; then
  echo "[ok] gmail.googleapis.com enabled"
else
  echo "[..] enabling gmail.googleapis.com"
  run gcloud services enable gmail.googleapis.com --project "$PROJECT_ID"
fi

C="https://console.cloud.google.com"
Q="?project=${PROJECT_ID}"
cat <<MSG

Done with the parts gcloud can automate. Finish these in the browser
(details and screenshots-free walkthrough: docs/google-cloud-setup.md):

  3. Configure the consent screen (Google Auth platform -> Get started / Branding):
       ${C}/auth/overview${Q}
     App name: email-town-labeler. Support + contact email: your address.
     Audience: Internal  if this is a Google Workspace account in your own organization;
               External  for personal @gmail.com accounts.

  4. Audience (External only): add yourself as a test user, or click "Publish app".
       ${C}/auth/audience${Q}
     Testing status => refresh tokens expire after 7 days (weekly re-login).
     In production (unverified, personal use) => no 7-day expiry; Google shows an
     "unverified app" warning you click through once.

  5. Data Access -> Add or remove scopes -> add:
       https://www.googleapis.com/auth/gmail.modify
       ${C}/auth/scopes${Q}

  6. Clients -> Create client -> Application type "Desktop app" -> Create -> Download JSON:
       ${C}/auth/clients/create${Q}

  7. Then:
       email-town-labeler setup          # it asks for the downloaded JSON file

MSG
