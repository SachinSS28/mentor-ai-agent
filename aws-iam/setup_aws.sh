#!/usr/bin/env bash
# Mentor — AWS setup: CLI profiles, KMS key, IAM roles + inline policies, MentorDeveloper permission set.
# Policies come from the JSON files in this folder (plan Appendix A).
#
# Usage:
#   ./setup_aws.sh --render-only   fill in placeholders locally, no AWS calls
#   ./setup_aws.sh --check         sign in, verify account, validate policies with IAM Access Analyzer; creates nothing
#   ./setup_aws.sh                 everything above, then create/update resources (safe to re-run)
set -euo pipefail
cd "$(dirname "$0")"

MODE="${1:-apply}"
case "$MODE" in --render-only|--check|apply) ;; *) echo "Unknown option: $MODE"; exit 1 ;; esac

ADMIN_PROFILE="mentor-admin"
DEV_PROFILE="mentor-dev"
KMS_ALIAS="alias/mentor"
AWS_CONFIG="${AWS_CONFIG_FILE:-$HOME/.aws/config}"
TASK_ROLES="mentor-agent-task mentor-sentinel-task mentor-worker-task"

say()  { printf '\n==> %s\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

# --- settings from .env (parsed, not executed) ---------------------------------------------
[ -f .env ] || fail ".env not found next to this script"
while IFS='=' read -r key value; do
  case "$key" in ''|\#*) continue ;; esac
  export "$key=$value"
done < <(grep -E '^[A-Z_]+=' .env)

AWS_REGION="${AWS_REGION:-ap-south-1}"
AWS_SSO_REGION="${AWS_SSO_REGION:-ap-south-1}"
AWS_SSO_ADMIN_ROLE="${AWS_SSO_ADMIN_ROLE:-AdministratorAccess}"

[[ "${AWS_ACCOUNT_ID:-}" =~ ^[0-9]{12}$ ]] || fail "AWS_ACCOUNT_ID in .env must be 12 digits"
if [ "$MODE" != "--render-only" ]; then
  [[ "${AWS_SSO_START_URL:-}" =~ ^https://.+ ]] || fail "AWS_SSO_START_URL in .env is missing (IAM Identity Center > Settings)"
  [ -n "${AWS_SSO_USERNAME:-}" ] || fail "AWS_SSO_USERNAME in .env is missing"
  command -v aws >/dev/null || fail "AWS CLI not installed (brew install awscli)"
fi
command -v jq >/dev/null || fail "jq not installed (brew install jq)"

# --- render policies ---------------------------------------------------------------------
render_policies() {
  local kms_key_id="$1"
  rm -rf rendered && mkdir rendered
  for f in mentor-*.json MentorDeveloper-permission-set.json task-roles-trust-policy.json; do
    sed -e "s/ACCOUNT_ID/$AWS_ACCOUNT_ID/g" -e "s/KMS_KEY_ID/$kms_key_id/g" "$f" > "rendered/$f"
    jq empty "rendered/$f" || fail "rendered/$f is not valid JSON"
  done
  # The execution role is only ever assumed by ECS, never from the Mac.
  jq '.Statement |= map(select(.Sid == "EcsTasksInThisAccount"))' \
    rendered/task-roles-trust-policy.json > rendered/execution-role-trust-policy.json
  if grep -lE 'ACCOUNT_ID|KMS_KEY_ID' rendered/*.json; then fail "placeholders left in the files above"; fi
  echo "Rendered policies in $(pwd)/rendered"
}

if [ "$MODE" = "--render-only" ]; then
  say "Rendering policies (no AWS calls)"
  render_policies "00000000-0000-0000-0000-000000000000"
  exit 0
fi

# --- CLI profile + browser sign-in -------------------------------------------------------
mkdir -p "$(dirname "$AWS_CONFIG")" && touch "$AWS_CONFIG"
if ! grep -q '^\[sso-session mentor\]' "$AWS_CONFIG"; then
  say "Adding the 'mentor' sign-in session to $AWS_CONFIG"
  cat >> "$AWS_CONFIG" <<EOF

[sso-session mentor]
sso_start_url = $AWS_SSO_START_URL
sso_region = $AWS_SSO_REGION
sso_registration_scopes = sso:account:access
EOF
fi
if ! grep -q "^\[profile $ADMIN_PROFILE\]" "$AWS_CONFIG"; then
  say "Adding profile $ADMIN_PROFILE"
  cat >> "$AWS_CONFIG" <<EOF

[profile $ADMIN_PROFILE]
sso_session = mentor
sso_account_id = $AWS_ACCOUNT_ID
sso_role_name = $AWS_SSO_ADMIN_ROLE
region = $AWS_REGION
output = json
EOF
fi

export AWS_PROFILE="$ADMIN_PROFILE"
if ! aws sts get-caller-identity >/dev/null 2>&1; then
  say "Opening your browser to sign in to AWS"
  aws sso login --profile "$ADMIN_PROFILE"
fi
SIGNED_IN_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
[ "$SIGNED_IN_ACCOUNT" = "$AWS_ACCOUNT_ID" ] || fail "signed in to account $SIGNED_IN_ACCOUNT, but .env says $AWS_ACCOUNT_ID"
say "Signed in as $(aws sts get-caller-identity --query Arn --output text)"

# --- validate every policy with IAM Access Analyzer --------------------------------------
validate() {  # file policy-type [resource-type]
  local findings
  findings=$(aws accessanalyzer validate-policy --region "$AWS_REGION" \
    --policy-type "$2" ${3:+--validate-policy-resource-type "$3"} \
    --policy-document "file://$1" \
    --query 'findings[].[findingType,issueCode,findingDetails]' --output text)
  if [ -z "$findings" ]; then echo "  OK  $(basename "$1")"; return 0; fi
  echo "  --  $(basename "$1")"; echo "$findings" | sed 's/^/      /'
  ! echo "$findings" | grep -qE '^(ERROR|SECURITY_WARNING)'
}

KMS_KEY_ID=$(aws kms describe-key --region "$AWS_REGION" --key-id "$KMS_ALIAS" \
  --query KeyMetadata.KeyId --output text 2>/dev/null || true)
render_policies "${KMS_KEY_ID:-00000000-0000-0000-0000-000000000000}"

say "Validating policies with IAM Access Analyzer"
ok=true
for f in rendered/mentor-*.json rendered/MentorDeveloper-permission-set.json; do
  validate "$f" IDENTITY_POLICY || ok=false
done
for f in rendered/task-roles-trust-policy.json rendered/execution-role-trust-policy.json; do
  validate "$f" RESOURCE_POLICY AWS::IAM::AssumeRolePolicyDocument || ok=false
done
$ok || fail "validation found errors or security warnings; nothing was created"

if [ "$MODE" = "--check" ]; then
  say "Check passed. Nothing was created. Run ./setup_aws.sh to create the resources."
  exit 0
fi

# --- KMS key -----------------------------------------------------------------------------
if [ -z "$KMS_KEY_ID" ]; then
  say "Creating KMS key $KMS_ALIAS (about US\$1/month)"
  KMS_KEY_ID=$(aws kms create-key --region "$AWS_REGION" --description "Mentor: secrets and files" \
    --tags TagKey=project,TagValue=mentor --query KeyMetadata.KeyId --output text)
  aws kms create-alias --region "$AWS_REGION" --alias-name "$KMS_ALIAS" --target-key-id "$KMS_KEY_ID"
  aws kms enable-key-rotation --region "$AWS_REGION" --key-id "$KMS_KEY_ID"
  render_policies "$KMS_KEY_ID"
else
  say "KMS key $KMS_ALIAS already exists ($KMS_KEY_ID)"
fi

# --- IAM roles ---------------------------------------------------------------------------
ensure_role() {  # name trust-file policy-file description
  if aws iam get-role --role-name "$1" >/dev/null 2>&1; then
    aws iam update-assume-role-policy --role-name "$1" --policy-document "file://$2"
    echo "  updated  $1"
  else
    aws iam create-role --role-name "$1" --assume-role-policy-document "file://$2" \
      --description "$4" --tags Key=project,Value=mentor >/dev/null
    echo "  created  $1"
  fi
  aws iam put-role-policy --role-name "$1" --policy-name "$1-inline" --policy-document "file://$3"
}

say "Creating IAM roles"
ensure_role mentor-ecs-execution rendered/execution-role-trust-policy.json \
  rendered/mentor-ecs-execution.json "Mentor: ECS pulls images, writes logs, injects app secrets"
ensure_role mentor-agent-task rendered/task-roles-trust-policy.json \
  rendered/mentor-agent-task.json "Mentor: agent, API and Telegram bot"
ensure_role mentor-sentinel-task rendered/task-roles-trust-policy.json \
  rendered/mentor-sentinel-task.json "Mentor: Sentinel gateway and MCP servers"
ensure_role mentor-worker-task rendered/task-roles-trust-policy.json \
  rendered/mentor-worker-task.json "Mentor: scheduler and Notion sync"

# --- MentorDeveloper permission set (IAM Identity Center) --------------------------------
say "Setting up the MentorDeveloper permission set"
SSO_ADMIN="aws sso-admin --region $AWS_SSO_REGION"
read -r INSTANCE_ARN IDENTITY_STORE_ID < <($SSO_ADMIN list-instances \
  --query 'Instances[0].[InstanceArn,IdentityStoreId]' --output text)
[ -n "$INSTANCE_ARN" ] && [ "$INSTANCE_ARN" != "None" ] || fail "IAM Identity Center is not enabled in $AWS_SSO_REGION"

PS_ARN=""
for arn in $($SSO_ADMIN list-permission-sets --instance-arn "$INSTANCE_ARN" --query 'PermissionSets[]' --output text); do
  name=$($SSO_ADMIN describe-permission-set --instance-arn "$INSTANCE_ARN" --permission-set-arn "$arn" \
    --query PermissionSet.Name --output text)
  if [ "$name" = "MentorDeveloper" ]; then PS_ARN="$arn"; break; fi
done
if [ -z "$PS_ARN" ]; then
  PS_ARN=$($SSO_ADMIN create-permission-set --instance-arn "$INSTANCE_ARN" --name MentorDeveloper \
    --description "Mentor local development on the Mac" --session-duration PT8H \
    --tags Key=project,Value=mentor --query PermissionSet.PermissionSetArn --output text)
  echo "  created  MentorDeveloper"
fi
$SSO_ADMIN put-inline-policy-to-permission-set --instance-arn "$INSTANCE_ARN" --permission-set-arn "$PS_ARN" \
  --inline-policy file://rendered/MentorDeveloper-permission-set.json

USER_ID=$(aws identitystore get-user-id --region "$AWS_SSO_REGION" --identity-store-id "$IDENTITY_STORE_ID" \
  --alternate-identifier "{\"UniqueAttribute\":{\"AttributePath\":\"userName\",\"AttributeValue\":\"$AWS_SSO_USERNAME\"}}" \
  --query UserId --output text) || fail "Identity Center user '$AWS_SSO_USERNAME' not found"

ASSIGNED=$($SSO_ADMIN list-account-assignments --instance-arn "$INSTANCE_ARN" --account-id "$AWS_ACCOUNT_ID" \
  --permission-set-arn "$PS_ARN" --query "AccountAssignments[?PrincipalId=='$USER_ID'] | length(@)" --output text)
if [ "$ASSIGNED" = "0" ]; then
  $SSO_ADMIN create-account-assignment --instance-arn "$INSTANCE_ARN" --permission-set-arn "$PS_ARN" \
    --target-type AWS_ACCOUNT --target-id "$AWS_ACCOUNT_ID" --principal-type USER --principal-id "$USER_ID" >/dev/null
  echo "  assigned MentorDeveloper to $AWS_SSO_USERNAME"
else
  $SSO_ADMIN provision-permission-set --instance-arn "$INSTANCE_ARN" --permission-set-arn "$PS_ARN" \
    --target-type ALL_PROVISIONED_ACCOUNTS >/dev/null
  echo "  re-provisioned MentorDeveloper (policy updated)"
fi

# --- local profiles: one per runtime role, so Mac containers get exactly their cloud permissions ---
add_profile() {  # name body
  grep -q "^\[profile $1\]" "$AWS_CONFIG" || printf '\n[profile %s]\n%s\n' "$1" "$2" >> "$AWS_CONFIG"
}
add_profile "$DEV_PROFILE" "sso_session = mentor
sso_account_id = $AWS_ACCOUNT_ID
sso_role_name = MentorDeveloper
region = $AWS_REGION"
for role in $TASK_ROLES; do
  add_profile "${role%-task}" "source_profile = $DEV_PROFILE
role_arn = arn:aws:iam::$AWS_ACCOUNT_ID:role/$role
region = $AWS_REGION"
done

say "Done"
cat <<EOF
  KMS key:   $KMS_ALIAS ($KMS_KEY_ID)
  Roles:     mentor-ecs-execution, $TASK_ROLES
  Profiles:  $ADMIN_PROFILE (admin), $DEV_PROFILE, mentor-agent, mentor-sentinel, mentor-worker

Test local dev access (Identity Center may take a minute to provision):
  aws sso login --profile $DEV_PROFILE
  aws sts get-caller-identity --profile mentor-agent
EOF
