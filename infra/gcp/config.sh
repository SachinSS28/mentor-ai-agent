# shellcheck shell=bash disable=SC2034  # sourced by the other scripts, which use these
# Shared by the infra/gcp scripts. us-west1 is one of the three free-tier regions.
REGION="us-west1"
ZONE="us-west1-b"
SERVER="mentor"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

die() { echo "$*" >&2; exit 1; }

env_value() { sed -n "s/^$1=//p" "$REPO_ROOT/.env" | tail -1; }

PROJECT="${MENTOR_GCP_PROJECT:-$(env_value MENTOR_GCP_PROJECT)}"
[ -n "$PROJECT" ] || die "Set MENTOR_GCP_PROJECT in .env to your Google Cloud project ID."

# SSH goes through Google's IAP tunnel: the server has no open ports.
on_server() {
  gcloud compute ssh "$SERVER" --project "$PROJECT" --zone "$ZONE" --tunnel-through-iap \
    --quiet -- "$@"
}
