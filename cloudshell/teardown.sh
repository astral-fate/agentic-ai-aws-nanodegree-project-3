#!/usr/bin/env bash
#
#  Tear down the Udacity nanodegree projects' AWS resources, across regions,
#  without touching anything else in the account.
#
#      bash teardown.sh                    inventory only - deletes NOTHING
#      bash teardown.sh --delete           delete what the inventory listed
#      bash teardown.sh --include-s3       also consider matching S3 buckets
#      bash teardown.sh --delete --include-s3
#      bash teardown.sh --regions us-east-1,eu-north-1
#      bash teardown.sh --extra my-prefix,other-prefix
#
#  ─────────────────────────────────────────────────────────────────────────
#  WHY IT IS BUILT THIS WAY
#
#  The account is named "Saudi Space Tech", so "delete everything not related
#  to Saudi Space" cannot be a filter - it would match the whole account. A
#  script that deletes whatever is NOT on a keep-list is one typo away from
#  destroying production, and AWS deletions do not come back.
#
#  So this is an allow-list. It deletes only resources whose NAME matches one
#  of TARGET_PATTERNS below, and prints everything else as [keep] so you can
#  see it was seen and deliberately left alone. If something you want gone is
#  not matched, add a pattern with --extra. Do not invert the logic.
#
#  That [keep] output is not decoration: a live run of this project's
#  PowerShell twin revealed four resources billing quietly -
#  customer_support_agent, customer_support_agent_mem, support_chatbot and
#  harness_support_chatbot - because AgentCore names some resources with
#  underscores where the console shows hyphens. They are in the list below now.
#
#  S3 BUCKETS ARE NEVER TOUCHED unless --include-s3 is passed, and even then
#  only buckets whose name matches a pattern. S3 is where real data lives.
#  ─────────────────────────────────────────────────────────────────────────
#
#  COST, in rough order of what actually bills while idle:
#    Bedrock Knowledge Bases + S3 Vectors indexes   the expensive idle ones
#    OpenSearch Serverless collections              ~$0.24/OCU-hr, min 2 OCU
#    AgentCore runtimes / memories / gateways
#    API Gateway, Lambda, DynamoDB, CloudWatch logs pennies, or free

set -uo pipefail

DELETE=0
INCLUDE_S3=0
FORCE=0
REGIONS="us-east-1,us-west-2,eu-west-1,eu-north-1"
EXTRA=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --delete)      DELETE=1 ;;
    --include-s3)  INCLUDE_S3=1 ;;
    --force)       FORCE=1 ;;
    --regions)     REGIONS="$2"; shift ;;
    --extra)       EXTRA="$2"; shift ;;
    -h|--help)     sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

# ── What counts as "ours" ────────────────────────────────────────────────────
TARGET_PATTERNS=(
  udacity-agentcore      # project 3 stack, tables, buckets, role, guardrail
  udacity_agentcore      # underscore variants (runtime, memory)
  novamart               # project 3 knowledge bases and gateway
  cs-agent               # project 2 prefix
  customer-support       # project 2 gateway / memory
  CustomerSupport        # project 2 CamelCase resources
  customer_support       # project 2 runtime + memory, underscore variant
  support-chatbot        # project 1 guardrail
  support_chatbot        # project 1 runtime + memory, underscore variant
  harness_support        # project 1 harness runtime
  order-tracker          # project 2 lambda
  refund-processor       # project 2 lambda
  bug-report             # project 1 tool stack, gateway, lambda, table
)
if [[ -n "$EXTRA" ]]; then
  IFS=',' read -ra _extra <<< "$EXTRA"
  TARGET_PATTERNS+=("${_extra[@]}")
fi

is_target() {
  local name="$1" p
  [[ -z "$name" || "$name" == "None" ]] && return 1
  for p in "${TARGET_PATTERNS[@]}"; do
    [[ "$name" == *"$p"* ]] && return 0
  done
  return 1
}

# ── Output ───────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; RED=$'\033[31m'
  YELLOW=$'\033[33m'; CYAN=$'\033[36m'; RESET=$'\033[0m'
else
  BOLD=""; DIM=""; GREEN=""; RED=""; YELLOW=""; CYAN=""; RESET=""
fi
head_()  { printf '\n%s=== %s%s\n' "$CYAN$BOLD" "$*" "$RESET"; }
hit()    { printf '   %s[TARGET]%s %s\n' "$YELLOW" "$RESET" "$*"; }
keep()   { printf '   %s[keep]   %s%s\n' "$DIM" "$*" "$RESET"; }
done_()  { printf '   %s[deleted]%s %s\n' "$GREEN" "$RESET" "$*"; }
fail()   { printf '   %s[FAILED]%s %s\n' "$RED" "$RESET" "$*"; }

# Queue of things to delete: one "region|kind|name|id" per line.
QUEUE=$(mktemp)
RESULTS=$(mktemp)
trap 'rm -f "$QUEUE" "$RESULTS"' EXIT

enqueue() { printf '%s|%s|%s|%s\n' "$1" "$2" "$3" "$4" >> "$QUEUE"; }

# aws wrapper: quiet, never fatal, empty on failure.
a() { aws "$@" 2>/dev/null || true; }

printf '\n%sAWS nanodegree teardown%s\n' "$BOLD" "$RESET"
ACCOUNT=$(a sts get-caller-identity --query Account --output text)
if [[ -z "$ACCOUNT" || "$ACCOUNT" == "None" ]]; then
  fail "No AWS credentials. Run this inside AWS CloudShell."
  exit 1
fi
printf '  account : %s\n' "$ACCOUNT"
printf '  identity: %s\n' "$(a sts get-caller-identity --query Arn --output text)"
printf '  regions : %s\n' "$REGIONS"
if [[ $DELETE -eq 1 ]]; then
  printf '  mode    : %sDELETE%s\n' "$RED" "$RESET"
else
  printf '  mode    : %sINVENTORY ONLY (nothing will be deleted)%s\n' "$GREEN" "$RESET"
fi
if [[ $INCLUDE_S3 -eq 1 ]]; then
  printf '  S3      : included (matching names only)\n'
else
  printf '  S3      : PROTECTED - never deleted\n'
fi

IFS=',' read -ra REGION_LIST <<< "$REGIONS"

for R in "${REGION_LIST[@]}"; do
  head_ "Region $R"

  # 1. Bedrock Knowledge Bases - bill for storage while idle
  while IFS=$'\t' read -r name id; do
    [[ -z "$name" ]] && continue
    if is_target "$name"; then hit "KnowledgeBase $name ($id)"; enqueue "$R" knowledge-base "$name" "$id"
    else keep "KnowledgeBase $name"; fi
  done < <(a bedrock-agent list-knowledge-bases --region "$R" \
            --query 'knowledgeBaseSummaries[*].[name,knowledgeBaseId]' --output text)

  # 2. S3 Vectors - the index behind each KB, also bills while idle
  while read -r vb; do
    [[ -z "$vb" ]] && continue
    if is_target "$vb"; then
      while read -r ix; do
        [[ -z "$ix" ]] && continue
        hit "S3VectorIndex $vb/$ix"; enqueue "$R" s3-vector-index "$vb/$ix" "$vb"
      done < <(a s3vectors list-indexes --vector-bucket-name "$vb" --region "$R" \
                --query 'indexes[*].indexName' --output text | tr '\t' '\n')
      hit "S3VectorBucket $vb"; enqueue "$R" s3-vector-bucket "$vb" "$vb"
    else keep "S3VectorBucket $vb"; fi
  done < <(a s3vectors list-vector-buckets --region "$R" \
            --query 'vectorBuckets[*].vectorBucketName' --output text | tr '\t' '\n')

  # 3. OpenSearch Serverless - bills hourly whether queried or not
  while IFS=$'\t' read -r name id; do
    [[ -z "$name" ]] && continue
    if is_target "$name"; then hit "OpenSearchCollection $name  <-- bills hourly"; enqueue "$R" opensearch "$name" "$id"
    else keep "OpenSearchCollection $name"; fi
  done < <(a opensearchserverless list-collections --region "$R" \
            --query 'collectionSummaries[*].[name,id]' --output text)

  # 4. AgentCore runtimes / memories / gateways
  while IFS=$'\t' read -r name id; do
    [[ -z "$name" ]] && continue
    if is_target "$name"; then hit "AgentCoreRuntime $name"; enqueue "$R" agentcore-runtime "$name" "$id"
    else keep "AgentCoreRuntime $name"; fi
  done < <(a bedrock-agentcore-control list-agent-runtimes --region "$R" \
            --query 'agentRuntimes[*].[agentRuntimeName,agentRuntimeId]' --output text)

  while IFS=$'\t' read -r id name; do
    [[ -z "$id" ]] && continue
    [[ -z "$name" || "$name" == "None" ]] && name="$id"
    if is_target "$name"; then hit "AgentCoreMemory $name"; enqueue "$R" agentcore-memory "$name" "$id"
    else keep "AgentCoreMemory $name"; fi
  done < <(a bedrock-agentcore-control list-memories --region "$R" \
            --query 'memories[*].[id,name]' --output text)

  while IFS=$'\t' read -r id name; do
    [[ -z "$id" ]] && continue
    [[ -z "$name" || "$name" == "None" ]] && name="$id"
    if is_target "$name"; then hit "AgentCoreGateway $name"; enqueue "$R" agentcore-gateway "$name" "$id"
    else keep "AgentCoreGateway $name"; fi
  done < <(a bedrock-agentcore-control list-gateways --region "$R" \
            --query 'items[*].[gatewayId,name]' --output text)

  # 5. Bedrock Guardrails
  while IFS=$'\t' read -r name id; do
    [[ -z "$name" ]] && continue
    if is_target "$name"; then hit "Guardrail $name"; enqueue "$R" guardrail "$name" "$id"
    else keep "Guardrail $name"; fi
  done < <(a bedrock list-guardrails --region "$R" --query 'guardrails[*].[name,id]' --output text)

  # 6. Lambda
  while read -r fn; do
    [[ -z "$fn" ]] && continue
    if is_target "$fn"; then hit "Lambda $fn"; enqueue "$R" lambda "$fn" "$fn"
    else keep "Lambda $fn"; fi
  done < <(a lambda list-functions --region "$R" --query 'Functions[*].FunctionName' --output text | tr '\t' '\n')

  # 7. API Gateway (REST)
  while IFS=$'\t' read -r name id; do
    [[ -z "$name" ]] && continue
    if is_target "$name"; then hit "ApiGateway $name"; enqueue "$R" apigateway "$name" "$id"
    else keep "ApiGateway $name"; fi
  done < <(a apigateway get-rest-apis --region "$R" --query 'items[*].[name,id]' --output text)

  # 8. DynamoDB
  while read -r t; do
    [[ -z "$t" ]] && continue
    if is_target "$t"; then hit "DynamoDB $t"; enqueue "$R" dynamodb "$t" "$t"
    else keep "DynamoDB $t"; fi
  done < <(a dynamodb list-tables --region "$R" --query 'TableNames' --output text | tr '\t' '\n')

  # 9. CloudWatch log groups
  while read -r lg; do
    [[ -z "$lg" ]] && continue
    is_target "$lg" && { hit "LogGroup $lg"; enqueue "$R" log-group "$lg" "$lg"; }
  done < <(a logs describe-log-groups --region "$R" --query 'logGroups[*].logGroupName' --output text | tr '\t' '\n')

  # 10. CloudFormation stacks - LAST, they own much of the above
  while read -r s; do
    [[ -z "$s" ]] && continue
    if is_target "$s"; then hit "CloudFormation $s"; enqueue "$R" cloudformation "$s" "$s"
    else keep "CloudFormation $s"; fi
  done < <(a cloudformation list-stacks --region "$R" \
            --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE ROLLBACK_COMPLETE UPDATE_ROLLBACK_COMPLETE \
            --query 'StackSummaries[*].StackName' --output text | tr '\t' '\n')
done

# 11. S3 - global, PROTECTED unless --include-s3
head_ "S3 buckets (global)"
while read -r b; do
  [[ -z "$b" ]] && continue
  if is_target "$b" && [[ $INCLUDE_S3 -eq 1 ]]; then
    hit "S3 $b"; enqueue global s3-bucket "$b" "$b"
  elif is_target "$b"; then
    keep "S3 $b  (matches a pattern - pass --include-s3 to delete)"
  else
    keep "S3 $b"
  fi
done < <(a s3api list-buckets --query 'Buckets[*].Name' --output text | tr '\t' '\n')

# ─────────────────────────────────────────────────────────────────────────────
COUNT=$(wc -l < "$QUEUE" | tr -d ' ')
printf '\n%s──────────────────────────────────────────────────────────────%s\n' "$BOLD" "$RESET"
if [[ "$COUNT" -eq 0 ]]; then
  printf ' %sNothing matched the project patterns. Nothing to delete.%s\n' "$GREEN" "$RESET"
  printf ' Anything listed as [keep] above was seen and left alone.\n\n'
  exit 0
fi

printf ' %s%s resource(s) matched the project patterns:%s\n' "$YELLOW" "$COUNT" "$RESET"
cut -d'|' -f2 "$QUEUE" | sort | uniq -c | while read -r n k; do printf '   %-22s %s\n' "$k" "$n"; done

if [[ $DELETE -eq 0 ]]; then
  printf '\n %sINVENTORY ONLY - nothing was deleted.%s\n' "$GREEN" "$RESET"
  printf ' Review the [TARGET] lines above. If they are all things you want gone:\n\n'
  printf '     bash %s --delete%s\n\n' "$(basename "$0")" "$([[ $INCLUDE_S3 -eq 1 ]] && echo ' --include-s3')"
  [[ $INCLUDE_S3 -eq 0 ]] && printf ' S3 buckets were NOT included. Add --include-s3 only if you are certain;\n bucket deletion removes every object and cannot be undone.\n\n'
  exit 0
fi

if [[ $FORCE -eq 0 ]]; then
  printf '\n %sAbout to DELETE the %s resources listed above. This cannot be undone.%s\n' "$RED" "$COUNT" "$RESET"
  printf ' Type DELETE to proceed: '
  read -r answer
  if [[ "$answer" != "DELETE" ]]; then
    printf ' %sCancelled. Nothing was deleted.%s\n\n' "$GREEN" "$RESET"
    exit 0
  fi
fi

printf '\n'
while IFS='|' read -r R KIND NAME ID; do
  [[ -z "$KIND" ]] && continue
  label="$KIND $NAME [$R]"
  ok=1
  case "$KIND" in
    knowledge-base)    a bedrock-agent delete-knowledge-base --knowledge-base-id "$ID" --region "$R" >/dev/null || ok=0 ;;
    s3-vector-index)   a s3vectors delete-index --vector-bucket-name "${NAME%%/*}" --index-name "${NAME##*/}" --region "$R" >/dev/null || ok=0 ;;
    s3-vector-bucket)  a s3vectors delete-vector-bucket --vector-bucket-name "$ID" --region "$R" >/dev/null || ok=0 ;;
    opensearch)        a opensearchserverless delete-collection --id "$ID" --region "$R" >/dev/null || ok=0 ;;
    agentcore-runtime) a bedrock-agentcore-control delete-agent-runtime --agent-runtime-id "$ID" --region "$R" >/dev/null || ok=0 ;;
    agentcore-memory)  a bedrock-agentcore-control delete-memory --memory-id "$ID" --region "$R" >/dev/null || ok=0 ;;
    agentcore-gateway) a bedrock-agentcore-control delete-gateway --gateway-identifier "$ID" --region "$R" >/dev/null || ok=0 ;;
    guardrail)         a bedrock delete-guardrail --guardrail-identifier "$ID" --region "$R" >/dev/null || ok=0 ;;
    lambda)            a lambda delete-function --function-name "$ID" --region "$R" >/dev/null || ok=0 ;;
    apigateway)        a apigateway delete-rest-api --rest-api-id "$ID" --region "$R" >/dev/null || ok=0 ;;
    dynamodb)          a dynamodb delete-table --table-name "$ID" --region "$R" >/dev/null || ok=0 ;;
    log-group)         a logs delete-log-group --log-group-name "$ID" --region "$R" >/dev/null || ok=0 ;;
    cloudformation)    a cloudformation delete-stack --stack-name "$ID" --region "$R" >/dev/null || ok=0 ;;
    s3-bucket)         a s3 rm "s3://$ID" --recursive >/dev/null; a s3api delete-bucket --bucket "$ID" >/dev/null || ok=0 ;;
    *) ok=0 ;;
  esac
  if [[ $ok -eq 1 ]]; then done_ "$label"; printf 'deleted|%s\n' "$label" >> "$RESULTS"
  else fail "$label"; printf 'FAILED|%s\n' "$label" >> "$RESULTS"; fi
done < "$QUEUE"

printf '\n%s──────────────────────────────────────────────────────────────%s\n' "$BOLD" "$RESET"
printf ' Summary\n'
while IFS='|' read -r outcome label; do
  if [[ "$outcome" == "deleted" ]]; then printf '   %s%-8s%s %s\n' "$GREEN" "$outcome" "$RESET" "$label"
  else printf '   %s%-8s%s %s\n' "$RED" "$outcome" "$RESET" "$label"; fi
done < "$RESULTS"

FAILED=$(grep -c '^FAILED' "$RESULTS" || true)
printf '\n'
if [[ "${FAILED:-0}" -gt 0 ]]; then
  printf ' %s%s deletion(s) failed - those resources may still bill.%s\n' "$RED" "$FAILED" "$RESET"
  printf ' Usually a CloudFormation stack still owns the resource, or it is mid-DELETE.\n'
  printf ' Wait a minute and re-run with --delete; the script is safe to repeat.\n'
else
  printf ' %sAll matched resources deleted.%s\n' "$GREEN" "$RESET"
fi
printf '\n Re-run without --delete to confirm the account is clean.\n'
printf ' Then check the Billing console after ~24h - it is the only real proof.\n\n'
