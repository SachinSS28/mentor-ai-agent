#!/usr/bin/env bash
# One-time setup of Mentor's always-on server on Google Cloud (plan D20). Safe to re-run:
# each step skips what already exists.
#
#   - e2-micro VM in us-west1 with a 30 GB standard disk: inside the free tier. Its public IPv4
#     address is billed (about US$3.65/month).
#   - Own network with no open ports; SSH only through Google's IAP tunnel.
#   - Daily snapshot of the disk, kept 7 days: the database's backup.
#   - Monthly budget alert on the billing account (50/80/100%, as in the charter).
#   - A Gemini API key restricted to the Gemini API, saved to .env (never printed).
set -euo pipefail
source "$(dirname "$0")/config.sh"

billing=$(gcloud billing projects describe "$PROJECT" --format='value(billingAccountName)')
[ -n "$billing" ] || die "Project $PROJECT has no billing account linked yet (see README)."
billing_id=${billing#billingAccounts/}

echo "Enabling APIs..."
gcloud services enable compute.googleapis.com iap.googleapis.com oslogin.googleapis.com \
  generativelanguage.googleapis.com apikeys.googleapis.com billingbudgets.googleapis.com \
  --project "$PROJECT"

if ! gcloud compute networks describe mentor --project "$PROJECT" >/dev/null 2>&1; then
  gcloud compute networks create mentor --project "$PROJECT" --subnet-mode=custom
  gcloud compute networks subnets create mentor-"$REGION" --project "$PROJECT" \
    --network=mentor --region="$REGION" --range=10.10.0.0/24
  # 35.235.240.0/20 is where IAP's tunnel connects from. Nothing else may connect in.
  gcloud compute firewall-rules create mentor-allow-iap-ssh --project "$PROJECT" \
    --network=mentor --direction=INGRESS --allow=tcp:22 --source-ranges=35.235.240.0/20
fi

if ! gcloud compute instances describe "$SERVER" --project "$PROJECT" --zone "$ZONE" \
  >/dev/null 2>&1; then
  # No service account: the bot uses its own keys, and a stolen server gets no Cloud access.
  gcloud compute instances create "$SERVER" --project "$PROJECT" --zone "$ZONE" \
    --machine-type=e2-micro --subnet=mentor-"$REGION" \
    --image-family=debian-13 --image-project=debian-cloud \
    --boot-disk-size=30GB --boot-disk-type=pd-standard \
    --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
    --no-service-account --no-scopes \
    --metadata=enable-oslogin=TRUE \
    --metadata-from-file=startup-script="$REPO_ROOT/infra/gcp/startup.sh"
fi

# 19:00 UTC is 03:00 in Hong Kong. Snapshots store only changed blocks (a few US cents a month)
# and survive the server being deleted. Postgres recovers from them like after a power cut.
if ! gcloud compute resource-policies describe mentor-daily --project "$PROJECT" \
  --region "$REGION" >/dev/null 2>&1; then
  gcloud compute resource-policies create snapshot-schedule mentor-daily --project "$PROJECT" \
    --region "$REGION" --daily-schedule --start-time=19:00 --max-retention-days=7 \
    --on-source-disk-delete=keep-auto-snapshots --storage-location="$REGION"
fi
if ! gcloud compute disks describe "$SERVER" --project "$PROJECT" --zone "$ZONE" \
  --format='value(resourcePolicies)' | grep -q '/mentor-daily$'; then
  gcloud compute disks add-resource-policies "$SERVER" --project "$PROJECT" --zone "$ZONE" \
    --resource-policies=mentor-daily
fi

if ! gcloud billing budgets list --billing-account="$billing_id" \
  --format='value(displayName)' | grep -qx "Mentor monthly"; then
  # Counts what you'd actually pay, after credits. Defaults to about US$10 in the account's
  # own currency; set MENTOR_BUDGET (e.g. 80HKD) to choose another amount.
  currency=$(gcloud billing accounts describe "$billing_id" --format='value(currencyCode)')
  case "$currency" in
    USD) default=10USD ;; HKD) default=80HKD ;; INR) default=850INR ;;
    *) default="" ;;
  esac
  amount=${MENTOR_BUDGET:-$default}
  [ -n "$amount" ] || die "Set MENTOR_BUDGET (billing currency is $currency), e.g. MENTOR_BUDGET=10$currency"
  gcloud billing budgets create --billing-account="$billing_id" --display-name="Mentor monthly" \
    --budget-amount="$amount" --filter-projects="projects/$PROJECT" \
    --threshold-rule=percent=0.5 --threshold-rule=percent=0.8 --threshold-rule=percent=1.0
fi

if [ -z "$(env_value MENTOR_GEMINI_API_KEY)" ]; then
  key="projects/$PROJECT/locations/global/keys/mentor-server"
  # Silenced: gcloud's "operation complete" message includes the new key.
  gcloud services api-keys describe "$key" >/dev/null 2>&1 ||
    gcloud services api-keys create --project "$PROJECT" --key-id=mentor-server \
      --display-name="Mentor server" --api-target=service=generativelanguage.googleapis.com \
      >/dev/null 2>&1 || die "Couldn't create the Gemini API key (see the Cloud console)."
  key_string=$(gcloud services api-keys get-key-string "$key" --format='value(keyString)')
  printf '\n# Gemini API key (create-server.sh), restricted to the Gemini API\nMENTOR_GEMINI_API_KEY=%s\n' \
    "$key_string" >> "$REPO_ROOT/.env"
  echo "Saved a new Gemini API key to .env."
fi

echo "Server ready. Next: infra/gcp/deploy.sh secrets && infra/gcp/deploy.sh google-token && infra/gcp/deploy.sh"
