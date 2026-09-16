#!/usr/bin/env bash
#
#  NovaMart Multi-Agent Customer Support — end-to-end AWS CloudShell deploy
#  ─────────────────────────────────────────────────────────────────────────
#  Self-contained. Every project file this deploy needs is embedded below;
#  nothing is cloned and nothing is downloaded except from AWS itself.
#  Paste this into AWS CloudShell and run it.
#
#     bash deploy-e2e-v08.sh              deploy everything, then grade it
#     bash deploy-e2e-v08.sh --status     show what exists, change nothing
#     bash deploy-e2e-v08.sh --test-only  re-run the grader against what is there
#     bash deploy-e2e-v08.sh --package    zip src/ + evidence for submission
#     bash deploy-e2e-v08.sh --teardown   delete everything it created
#
#  ─────────────────────────────────────────────────────────────────────────
#  COST — read this before running
#
#    DynamoDB, S3 objects, Lambda-free architecture      cents, or free
#    Bedrock model invocations                           per token, small
#    Bedrock Knowledge Base storage + S3 Vectors index   BILLS WHILE IDLE
#
#  The last line is the one that costs real money if you forget about it.
#  A Knowledge Base with an S3 Vectors index left running is not free just
#  because nothing is querying it. Finish, screenshot, then immediately:
#
#     bash deploy-e2e-v08.sh --teardown
#
#  The script prints that reminder again at the end.
#  ─────────────────────────────────────────────────────────────────────────
#
#  DEPENDENCIES — installed automatically, nothing to do by hand
#
#  CloudShell's python3 ships boto3 but not strands-agents or python-dotenv,
#  both of which config.py and src/agent_orchestrator.py import unconditionally.
#  Phase 1 below creates a dedicated virtualenv (~/.novamart-venv) and installs
#  requirements.txt into it — not `pip install --user`, because CloudShell's
#  own python3 is itself already inside a virtualenv where `--user` fails
#  outright. Every later phase runs project code through that venv's python3.
#  ─────────────────────────────────────────────────────────────────────────
#
#  STATUS — read this too
#
#  This script HAS now been executed against a live AWS account. It deployed
#  the CloudFormation stack, seeded the tables, provisioned the S3 Vectors
#  bucket and three indexes, created and synced three Knowledge Bases,
#  deployed the AgentCore runtime with the guardrail attached, and scored
#  120/120 on the Udacity grader. That run is committed as evidence/run-02.
#
#  What is still NOT verified live:
#    - --teardown (the cleanup ownership fix landed after the live run)
#    - the adversarial suite's and scenario runner's live verdicts. Both
#      failed on earlier runs because the starter's invoke_agent() called
#      invoke_agent_runtime with sessionId/inputText; the real API takes
#      agentRuntimeArn plus a payload blob and a 33-char runtimeSessionId.
#      That is corrected and guarded by an offline test, but the corrected
#      call has not yet been observed succeeding against AWS. It was
#      initially misdiagnosed as missing Bedrock model access — it was not.
#
#  Every AWS-mutating call is still treated as fallible — a failure prints
#  the exact console steps for that one piece and the script carries on,
#  rather than claiming success it cannot verify. The summary table at the
#  end of the run is the authority on what actually happened.
#
#  Resumable. State lives in ~/.novamart-state; re-running skips whatever
#  already exists, so a dropped CloudShell session costs nothing but time.

set -uo pipefail

# This file is a TEMPLATE, not the deliverable. The embedded project files are
# substituted in by scripts/build_cloudshell_script.py, which writes
# cloudshell/deploy-e2e-<version>.sh. Running the template directly writes no
# project files and would silently reuse whatever happens to already be on
# disk — so refuse instead.
#
# This is the template, not the runnable script.
if grep -q '^__EMBEDDED''_FILES__$' "${BASH_SOURCE[0]}" 2>/dev/null; then
  cat >&2 <<'REFUSE'
This is the template, not the runnable script.

  Run the generated one instead, e.g.:

    bash cloudshell/deploy-e2e-v08.sh

REFUSE
  exit 2
fi

# ── Configuration ────────────────────────────────────────────────────────────
# Bumped on every fix. The generated file is named deploy-e2e-<version>.sh and
# the banner prints it, so an uploaded copy can never be confused with an
# older one sitting in the same directory.
SCRIPT_VERSION="v08"

REGION="${AWS_REGION:-us-east-1}"

PROJECT_DIR="${HOME}/novamart-project"
STATE_DIR="${HOME}/.novamart-state"
EVIDENCE_DIR="${PROJECT_DIR}/evidence/live"
ENV_FILE="${PROJECT_DIR}/.env"

# Every project-python invocation below goes through $PY, not the bare
# `python3` on PATH. It starts as the system interpreter and is only
# repointed at the venv once install_dependencies() has actually verified
# the packages import there — so a script that never reaches that phase (or
# whose venv creation fails) still runs seed_data.py, which needs nothing
# beyond boto3, with whatever python3 CloudShell already provides.
PY="python3"
VENV_DIR="${HOME}/.novamart-venv"

mkdir -p "$STATE_DIR"

# ── Output ───────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; RED=$'\033[31m'
  YELLOW=$'\033[33m'; CYAN=$'\033[36m'; RESET=$'\033[0m'
else
  BOLD=""; DIM=""; GREEN=""; RED=""; YELLOW=""; CYAN=""; RESET=""
fi

PHASE_N=0
phase() { PHASE_N=$((PHASE_N+1)); printf '\n%s━━ %d. %s%s\n' "$CYAN$BOLD" "$PHASE_N" "$*" "$RESET"; }
ok()    { printf '   %s✓%s %s\n' "$GREEN" "$RESET" "$*"; }
skip()  { printf '   %s·%s %s\n' "$DIM" "$RESET" "${DIM}$*${RESET}"; }
warn()  { printf '   %s!%s %s\n' "$YELLOW" "$RESET" "$*"; }
bad()   { printf '   %s✗%s %s\n' "$RED" "$RESET" "$*"; }
die()   { bad "$*"; printf '\n%sStopped. Nothing further was attempted.%s\n' "$RED" "$RESET"; exit 1; }

save()  { printf '%s' "$2" > "$STATE_DIR/$1"; }
load()  { cat "$STATE_DIR/$1" 2>/dev/null || true; }
have()  { [[ -n "$(load "$1")" ]]; }

# Idempotent .env writer — replaces any existing line for KEY rather than
# appending a duplicate, so a resumed run (which skips work already done)
# still leaves .env correct instead of accumulating stale/duplicate lines.
env_set() {
  local key="$1" value="$2"
  mkdir -p "$(dirname "$ENV_FILE")"
  touch "$ENV_FILE"
  grep -v "^${key}=" "$ENV_FILE" > "${ENV_FILE}.tmp" 2>/dev/null || true
  mv "${ENV_FILE}.tmp" "$ENV_FILE"
  printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
}

# Records which phases worked, for the summary table.
RESULTS=()
record() { RESULTS+=("$1|$2|$3"); }

# Read a `NAME = "literal"` module-level assignment out of a project .py file.
# Used so the template never hardcodes a model ID or resource name — it reads
# config.py at run time instead, exactly like the agent code itself does.
cfg_str() {
  local key="$1"
  grep -E "^${key}[[:space:]]*=" "${PROJECT_DIR}/config.py" 2>/dev/null | head -1 \
    | sed -E 's/^[A-Za-z_]+[[:space:]]*=[[:space:]]*"([^"]*)".*/\1/'
}

# Read the fallback literal out of `NAME = os.environ.get('ENVVAR', 'literal')`.
cfg_env_default() {
  local key="$1"
  grep -E "^${key}[[:space:]]*=[[:space:]]*os\.environ\.get" "${PROJECT_DIR}/config.py" 2>/dev/null | head -1 \
    | sed -E "s/.*os\.environ\.get\('[^']*',[[:space:]]*'([^']*)'\).*/\1/"
}

banner() {
  printf '%s\n' "${BOLD}NovaMart Multi-Agent Customer Support — end-to-end deploy ${SCRIPT_VERSION}${RESET}"
  printf '%s\n' "${DIM}running: ${BASH_SOURCE[0]}${RESET}"
  printf '%s\n' "${DIM}region $REGION · project $PROJECT_NAME · state $STATE_DIR${RESET}"
  printf '\n%s%s%s\n' "$YELLOW" "STATUS" "$RESET"
  printf '%s\n' "${DIM}This script HAS been run against a live AWS account: it deployed the full${RESET}"
  printf '%s\n' "${DIM}stack and scored 120/120 on the Udacity grader (evidence/run-02).${RESET}"
  printf '%s
' "${DIM}Not yet exercised live: --teardown, and the adversarial/scenario runners${RESET}"
  printf '%s
' "${DIM}(their invoke_agent call was corrected after the last run, not yet proven).${RESET}"
  printf '%s\n' "${DIM}Every AWS call is still treated as fallible: a failure prints console steps${RESET}"
  printf '%s\n' "${DIM}for that one piece and the run continues. Trust the summary table below.${RESET}"
  printf '\n%s%s%s\n' "$YELLOW" "COST WARNING" "$RESET"
  printf '%s\n' "${DIM}Bedrock Knowledge Base storage and its S3 Vectors index bill while idle,${RESET}"
  printf '%s\n' "${DIM}whether or not anything queries them. Run --teardown after screenshotting.${RESET}"
}

# ═════════════════════════════════════════════════════════════════════════════
#  0. Write the embedded project files
# ═════════════════════════════════════════════════════════════════════════════
materialise() {
  phase "Writing project files to $PROJECT_DIR"
  mkdir -p "$PROJECT_DIR/src" "$PROJECT_DIR/tests" "$PROJECT_DIR/infrastructure" \
           "$PROJECT_DIR/scripts" "$EVIDENCE_DIR"

__EMBEDDED_FILES__

  ok "config.py"
  ok "requirements.txt"
  ok "src/agent_orchestrator.py ($(wc -l < "$PROJECT_DIR/src/agent_orchestrator.py" 2>/dev/null) lines)"
  ok "src/agent_utils.py"
  ok "src/agent_observability.py"
  ok "src/bedrock_kb_retrieval.py"
  ok "src/demo.py"
  ok "tests/test_agent.py"
  ok "infrastructure/starter_stack.yaml"
  ok "infrastructure/seed_data.py"
  ok "infrastructure/cleanup.py"
  ok "scripts/_pathutil.py"
  ok "scripts/run_adversarial.py"
  ok "scripts/run_scenarios.py"
  record "Project files" "OK" "$PROJECT_DIR"

  # Resolved after materialise, since they are read out of the embedded
  # config.py rather than hardcoded here.
  PROJECT_NAME="${PROJECT_NAME:-$(cfg_env_default PROJECT_NAME)}"
  PROJECT_NAME="${PROJECT_NAME:-udacity-agentcore}"
  STACK_NAME="$PROJECT_NAME"
  ORCH_MODEL_ID="$(cfg_str ORCHESTRATOR_MODEL_ID)"
  WORKER_MODEL_ID_VAL="$(cfg_str WORKER_MODEL_ID)"
}

# ═════════════════════════════════════════════════════════════════════════════
#  1. Install Python dependencies
# ═════════════════════════════════════════════════════════════════════════════
# CloudShell's python3 ships boto3/botocore but not strands-agents or
# python-dotenv — both are on the import path of the phases that actually
# deploy and grade the agent: config.py does an unconditional
# `from dotenv import load_dotenv`, and src/agent_orchestrator.py imports
# `strands` / `strands.models`. Every script that reaches those imports
# (agent_orchestrator.py, tests/test_agent.py, infrastructure/cleanup.py —
# all three `import config`) needs this phase to have succeeded first.
#
# Installed into a dedicated venv rather than `pip install --user`:
# CloudShell's own python3 is itself already inside a virtualenv, where user
# site-packages are invisible to pip, so `--user` fails outright there. This
# was learned the hard way in the sibling project's live run
# (../agentic-ai-aws-nanodegree-project-2), which uses the same venv
# approach for its own toolkit install.
dependency_console_steps() {
  cat <<EOF

   ${BOLD}Install the dependencies by hand instead:${RESET}
     python3 -m venv $VENV_DIR
     $VENV_DIR/bin/pip install --upgrade pip
     $VENV_DIR/bin/pip install -r $PROJECT_DIR/requirements.txt
     Then re-run: bash ${BASH_SOURCE[0]}

EOF
}

install_dependencies() {
  phase "Installing Python dependencies (requirements.txt)"

  if [[ -x "${VENV_DIR}/bin/python3" ]] \
     && "${VENV_DIR}/bin/python3" -c "import strands, dotenv" >/dev/null 2>&1; then
    PY="${VENV_DIR}/bin/python3"
    skip "strands-agents and python-dotenv already importable in $VENV_DIR"
    record "Dependencies" "OK" "already installed in $VENV_DIR"
    return 0
  fi

  if [[ ! -x "${VENV_DIR}/bin/pip" ]]; then
    printf '   %s⋯%s creating a virtualenv at %s ' "$DIM" "$RESET" "$VENV_DIR"
    if python3 -m venv "$VENV_DIR" >/tmp/novamart-venv.log 2>&1; then
      printf '%s✓%s\n' "$GREEN" "$RESET"
    else
      printf '%s✗%s\n' "$RED" "$RESET"
      bad "could not create the virtualenv:"
      tail -10 /tmp/novamart-venv.log | sed 's/^/       /'
      dependency_console_steps
      record "Dependencies" "FAILED" "could not create $VENV_DIR"
      return 1
    fi
  fi

  local out
  if out="$( "${VENV_DIR}/bin/pip" install --quiet --upgrade pip 2>&1 \
             && "${VENV_DIR}/bin/pip" install --quiet -r "$PROJECT_DIR/requirements.txt" 2>&1 )"; then
    ok "pip install completed in $VENV_DIR"
  else
    bad "pip install failed:"
    tail -20 <<<"$out" | sed 's/^/       /'
    dependency_console_steps
    record "Dependencies" "FAILED" "see console steps above"
    return 1
  fi

  # Verified, not assumed: a zero exit from pip does not guarantee the
  # packages are importable by the interpreter that will actually run the
  # agent code — check that directly before trusting it.
  if "${VENV_DIR}/bin/python3" -c "import strands, dotenv" >/dev/null 2>&1; then
    PY="${VENV_DIR}/bin/python3"
    ok "import strands, dotenv — verified in $VENV_DIR"
    record "Dependencies" "OK" "$VENV_DIR"
  else
    bad "pip reported success but 'import strands, dotenv' still fails in $VENV_DIR"
    dependency_console_steps
    record "Dependencies" "FAILED" "installed but not importable — check python3/pip versions"
    return 1
  fi
}

# ═════════════════════════════════════════════════════════════════════════════
#  2. Preflight
# ═════════════════════════════════════════════════════════════════════════════
preflight() {
  phase "Preflight"

  command -v aws     >/dev/null || die "aws CLI not found. Run this inside AWS CloudShell."
  command -v python3  >/dev/null || die "python3 not found."
  command -v jq       >/dev/null || warn "jq not found — some output parsing will be skipped."
  command -v zip       >/dev/null || warn "zip not found — --package will fail later."

  if [[ "$PY" == "${VENV_DIR}/bin/python3" ]]; then
    ok "dependencies (strands-agents, python-dotenv) installed automatically at $VENV_DIR"
  else
    warn "dependencies are not yet installed — phase 1 (Install Python dependencies)"
    warn "installs them automatically into $VENV_DIR; see its output above if it failed."
  fi

  local identity account arn
  identity="$(aws sts get-caller-identity --output json 2>/dev/null)" \
    || die "No AWS credentials. In CloudShell these are already configured."
  account="$(jq -r .Account 2>/dev/null <<<"$identity")"
  arn="$(jq -r .Arn 2>/dev/null <<<"$identity")"
  save account "$account"
  save caller_arn "$arn"
  ok "account $account"
  ok "identity $arn"
  ok "region $REGION"

  case "$arn" in
    *":root")
      warn "Running as the account root. Prefer an IAM user or role for this." ;;
  esac

  # Model access. Checked up front — ORCH_MODEL_ID / WORKER_MODEL_ID_VAL are
  # read out of the embedded config.py (materialise() sets them), never
  # hardcoded here. These are inference-profile IDs (the "us." prefix), so
  # list-foundation-models — which enumerates base model IDs — may not match
  # them exactly; a miss here is a warning, not a failure.
  if aws bedrock list-foundation-models --region "$REGION" \
       --query "modelSummaries[?modelId=='${ORCH_MODEL_ID}' || modelId=='${WORKER_MODEL_ID_VAL}'].modelId" \
       --output text 2>/dev/null | grep -q .; then
    ok "orchestrator/worker foundation models visible"
  else
    warn "Could not confirm ${ORCH_MODEL_ID} / ${WORKER_MODEL_ID_VAL} via list-foundation-models."
    warn "These may be cross-region inference profile IDs rather than base model IDs —"
    warn "check Bedrock console → Model access if agent calls fail with AccessDenied."
  fi

  check_permissions
}

# Report every missing permission at once, before the first AWS write.
check_permissions() {
  local missing=()
  aws cloudformation describe-stacks --region "$REGION" >/dev/null 2>&1 \
    || missing+=("cloudformation:DescribeStacks              the starter stack")
  aws dynamodb list-tables --region "$REGION" --max-items 1 >/dev/null 2>&1 \
    || missing+=("dynamodb:ListTables                        orders/customers/workflow tables")
  aws s3api list-buckets >/dev/null 2>&1 \
    || missing+=("s3:ListAllMyBuckets                        policy + vector buckets")
  aws s3vectors list-vector-buckets --region "$REGION" >/dev/null 2>&1 \
    || missing+=("s3vectors:ListVectorBuckets                the Knowledge Base vector store")
  aws bedrock list-guardrails --region "$REGION" >/dev/null 2>&1 \
    || missing+=("bedrock:ListGuardrails                     the enterprise guardrail")
  aws bedrock-agent list-knowledge-bases --region "$REGION" >/dev/null 2>&1 \
    || missing+=("bedrock-agent:ListKnowledgeBases           the three policy KBs")
  aws bedrock-agentcore-control list-agent-runtimes --region "$REGION" >/dev/null 2>&1 \
    || missing+=("bedrock-agentcore:ListAgentRuntimes        the deployed agent runtime")
  aws logs describe-log-groups --region "$REGION" --limit 1 >/dev/null 2>&1 \
    || missing+=("logs:DescribeLogGroups                     agent CloudWatch logs")
  aws xray get-sampling-rules --region "$REGION" >/dev/null 2>&1 \
    || missing+=("xray:GetSamplingRules                      distributed tracing")

  if [[ ${#missing[@]} -eq 0 ]]; then
    ok "all required permissions present"
    return 0
  fi

  bad "This identity cannot deploy the project. Missing:"
  printf '\n'
  printf '       %s\n' "${missing[@]}"
  cat <<EOF

   Nothing has been created — this check runs before the first write.

   Use the Udacity Cloud Lab credentials (Cloud Resources tab → generate
   access keys), or any principal with the permissions above in $REGION.

     export AWS_ACCESS_KEY_ID=...
     export AWS_SECRET_ACCESS_KEY=...
     export AWS_SESSION_TOKEN=...
     export AWS_REGION=$REGION

EOF
  exit 1
}

# ═════════════════════════════════════════════════════════════════════════════
#  3. CloudFormation
# ═════════════════════════════════════════════════════════════════════════════
stack_console_steps() {
  cat <<EOF

   ${BOLD}Deploy the stack by hand instead:${RESET}
     CloudFormation console → Create stack → With new resources
       Template   $PROJECT_DIR/infrastructure/starter_stack.yaml
       Stack name $STACK_NAME
       Parameters ProjectName=$PROJECT_NAME
       Capability CAPABILITY_NAMED_IAM
     Then re-run: bash ${BASH_SOURCE[0]}

EOF
}

fetch_stack_outputs() {
  local json value out_key
  json="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
    --query 'Stacks[0].Outputs' --output json 2>/dev/null)"
  if [[ -z "$json" || "$json" == "null" ]]; then
    warn "could not read stack outputs"
    return 1
  fi

  for out_key in OrdersTableName CustomersTableName WorkflowStateTableName \
                 PolicyDocumentsBucketName VectorStoreBucketName \
                 AgentCoreRoleArn AgentLogGroupName; do
    value="$(jq -r --arg k "$out_key" '.[] | select(.OutputKey==$k) | .OutputValue' <<<"$json" 2>/dev/null)"
    [[ -n "$value" && "$value" != "null" ]] && save "out_${out_key}" "$value"
  done

  save policy_bucket      "$(load out_PolicyDocumentsBucketName)"
  save vector_bucket      "$(load out_VectorStoreBucketName)"
  save agentcore_role_arn "$(load out_AgentCoreRoleArn)"

  ok "policy bucket   $(load policy_bucket)"
  ok "vector bucket   $(load vector_bucket)"
  ok "AgentCore role  $(load agentcore_role_arn)"
}

deploy_stack() {
  phase "CloudFormation stack ($STACK_NAME)"

  local status
  status="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
    --query 'Stacks[0].StackStatus' --output text 2>/dev/null)"

  if [[ "$status" == *_COMPLETE && "$status" != "ROLLBACK_COMPLETE" ]]; then
    skip "stack $STACK_NAME exists ($status)"
  else
    local out
    if out="$(aws cloudformation deploy \
        --template-file "$PROJECT_DIR/infrastructure/starter_stack.yaml" \
        --stack-name "$STACK_NAME" \
        --parameter-overrides "ProjectName=$PROJECT_NAME" \
        --capabilities CAPABILITY_NAMED_IAM \
        --region "$REGION" 2>&1)"; then
      ok "stack $STACK_NAME deployed"
    elif grep -qi "no changes" <<<"$out"; then
      skip "stack $STACK_NAME already up to date"
    else
      bad "cloudformation deploy failed:"
      tail -20 <<<"$out" | sed 's/^/       /'
      stack_console_steps
      record "CloudFormation" "FAILED" "see console steps above"
      return 1
    fi
  fi

  status="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
    --query 'Stacks[0].StackStatus' --output text 2>/dev/null)"
  if [[ "$status" != *_COMPLETE || "$status" == "ROLLBACK_COMPLETE" ]]; then
    bad "stack status is ${status:-UNKNOWN}, not complete"
    stack_console_steps
    record "CloudFormation" "FAILED" "status=${status:-UNKNOWN}"
    return 1
  fi

  fetch_stack_outputs
  ok "stack status $status"
  record "CloudFormation" "OK" "$STACK_NAME"
}

# ═════════════════════════════════════════════════════════════════════════════
#  4. Seed data
# ═════════════════════════════════════════════════════════════════════════════
seed_console_steps() {
  cat <<EOF

   ${BOLD}Seed by hand instead:${RESET}
     $PY infrastructure/seed_data.py  (from $PROJECT_DIR, with AWS creds set)
     or, at minimum, upload the three policy documents so the Knowledge
     Bases have something to retrieve:
       s3://$(load policy_bucket)/policies/returns/return_policy.txt
       s3://$(load policy_bucket)/policies/shipping/shipping_policy.txt
       s3://$(load policy_bucket)/policies/warranty/warranty_policy.txt

EOF
}

seed_data_phase() {
  phase "Seeding DynamoDB + policy documents (infrastructure/seed_data.py)"

  local out
  if out="$( cd "$PROJECT_DIR" && AWS_REGION="$REGION" PROJECT_NAME="$PROJECT_NAME" \
      "$PY" infrastructure/seed_data.py 2>&1 )"; then
    ok "seed_data.py completed"
    tail -6 <<<"$out" | sed 's/^/       /'
    record "Seed data" "OK" "customers, orders, policy docs"
  else
    bad "seed_data.py failed:"
    tail -20 <<<"$out" | sed 's/^/       /'
    seed_console_steps
    record "Seed data" "FAILED" "see console steps above"
    return 1
  fi
}

# ═════════════════════════════════════════════════════════════════════════════
#  5. Knowledge Bases ×3 — the phase with no project-2 precedent
# ═════════════════════════════════════════════════════════════════════════════
# Titan Embed Text v2 is the embedding model src/bedrock_kb_retrieval.py's own
# module docstring specifies for all three Knowledge Bases. config.py has no
# constant for it (embedding choice belongs to the Knowledge Base, not the
# agent code), so it is named once here rather than invented per-call.
EMBED_MODEL_ID="amazon.titan-embed-text-v2:0"
EMBED_DIMENSION=1024

vector_bucket_console_steps() {
  local vb="$1"
  cat <<EOF

   ${BOLD}Create the S3 Vectors bucket by hand instead:${RESET}
     S3 console → Vector buckets → Create vector bucket
       Name   $vb
     Then re-run: bash ${BASH_SOURCE[0]}

EOF
}

index_console_steps() {
  local vb="$1" index="$2"
  cat <<EOF

   ${BOLD}Create the ${index} index by hand instead:${RESET}
     S3 console → Vector buckets → $vb → Create vector index
       Name              ${index}
       Dimensions        ${EMBED_DIMENSION}
       Distance metric   Cosine
       Data type         float32

EOF
}

# The CFN stack provisions a general-purpose S3 bucket named for vector
# storage and grants s3vectors:* on it, but does not itself create a native
# S3 Vectors vector-bucket or its indexes — those are a distinct resource
# type with no CloudFormation coverage in starter_stack.yaml, so this
# function creates them against the S3 Vectors API before any Knowledge Base
# can reference them.
ensure_vector_infra() {
  phase "S3 Vectors bucket and indexes"

  local vb; vb="$(load vector_bucket)"
  if [[ -z "$vb" ]]; then
    bad "no vector bucket name — CloudFormation phase did not complete"
    record "S3 Vectors bucket" "SKIPPED" "missing CloudFormation output"
    return 1
  fi

  if aws s3vectors get-vector-bucket --vector-bucket-name "$vb" --region "$REGION" >/dev/null 2>&1; then
    skip "vector bucket $vb exists"
  else
    if aws s3vectors create-vector-bucket --vector-bucket-name "$vb" --region "$REGION" >/dev/null 2>&1; then
      ok "created vector bucket $vb"
    else
      bad "could not create S3 Vectors bucket $vb"
      vector_bucket_console_steps "$vb"
      record "S3 Vectors bucket" "MANUAL" "see console steps above"
      return 1
    fi
  fi

  local vb_arn
  vb_arn="$(aws s3vectors get-vector-bucket --vector-bucket-name "$vb" --region "$REGION" \
    --query 'vectorBucket.vectorBucketArn' --output text 2>/dev/null)"
  save vector_bucket_arn "$vb_arn"
  ok "$vb_arn"

  local idx all_ok=1
  for idx in returns-policy-index shipping-policy-index warranty-policy-index; do
    if aws s3vectors get-index --vector-bucket-name "$vb" --index-name "$idx" --region "$REGION" >/dev/null 2>&1; then
      skip "index $idx exists"
    elif aws s3vectors create-index --vector-bucket-name "$vb" --index-name "$idx" \
        --data-type float32 --dimension "$EMBED_DIMENSION" --distance-metric cosine \
        --region "$REGION" >/dev/null 2>&1; then
      ok "created index $idx"
    else
      bad "could not create index $idx"
      index_console_steps "$vb" "$idx"
      all_ok=0
    fi
  done

  if [[ "$all_ok" -eq 1 ]]; then
    record "S3 Vectors bucket/indexes" "OK" "$vb"
  else
    record "S3 Vectors bucket/indexes" "PARTIAL" "$vb — see console steps above"
  fi
}

kb_console_steps() {
  local domain="$1" index="$2" var="$3"
  cat <<EOF

   ${BOLD}Create the ${domain} Knowledge Base by hand instead:${RESET}
     Bedrock console → Knowledge Bases → Create
       Name              novamart-${domain}-policy-kb
       IAM role          $(load agentcore_role_arn)
       Embedding model   Titan Text Embeddings V2
       Vector store      S3 Vectors → existing bucket $(load vector_bucket), index ${index}
       Data source       s3://$(load policy_bucket)/policies/${domain}/
     Sync the data source, then:
       echo '<kb-id>' > $STATE_DIR/kb_${domain}
       echo '${var}=<kb-id>' >> $ENV_FILE

EOF
}

create_kb() {
  local domain="$1" index="$2" var="$3"
  local kb_id; kb_id="$(load "kb_${domain}")"
  if [[ -n "$kb_id" ]]; then
    skip "KB ${domain} exists (${kb_id})"
    env_set "$var" "$kb_id"
    return 0
  fi

  local vb_arn policy_bucket role_arn
  vb_arn="$(load vector_bucket_arn)"
  policy_bucket="$(load policy_bucket)"
  role_arn="$(load agentcore_role_arn)"

  if [[ -z "$vb_arn" || -z "$policy_bucket" || -z "$role_arn" ]]; then
    bad "missing a prerequisite for the ${domain} KB (vector bucket / policy bucket / role)"
    kb_console_steps "$domain" "$index" "$var"
    record "KB ${domain}" "SKIPPED" "missing prerequisite"
    return 1
  fi

  kb_id=$(aws bedrock-agent create-knowledge-base \
    --name "novamart-${domain}-policy-kb" \
    --role-arn "$role_arn" \
    --knowledge-base-configuration "$(cat <<JSON
{"type":"VECTOR",
 "vectorKnowledgeBaseConfiguration":{
   "embeddingModelArn":"arn:aws:bedrock:${REGION}::foundation-model/${EMBED_MODEL_ID}"}}
JSON
)" \
    --storage-configuration "$(cat <<JSON
{"type":"S3_VECTORS",
 "s3VectorsConfiguration":{
   "vectorBucketArn":"${vb_arn}",
   "indexName":"${index}"}}
JSON
)" \
    --region "$REGION" \
    --query 'knowledgeBase.knowledgeBaseId' --output text 2>/dev/null) || {
      bad "Could not create the ${domain} Knowledge Base"
      kb_console_steps "$domain" "$index" "$var"
      record "KB ${domain}" "MANUAL" "see console steps above"
      return 1
    }

  save "kb_${domain}" "$kb_id"
  ok "KB ${domain} = ${kb_id}"

  local ds_id
  ds_id=$(aws bedrock-agent create-data-source \
    --knowledge-base-id "$kb_id" \
    --name "${domain}-policy-docs" \
    --data-source-configuration "$(cat <<JSON
{"type":"S3",
 "s3Configuration":{
   "bucketArn":"arn:aws:s3:::${policy_bucket}",
   "inclusionPrefixes":["policies/${domain}/"]}}
JSON
)" \
    --region "$REGION" \
    --query 'dataSource.dataSourceId' --output text 2>/dev/null) || {
      bad "Could not create the data source for ${domain}"
      record "KB ${domain}" "PARTIAL" "KB created, data source failed"
      return 1
    }
  save "ds_${domain}" "$ds_id"
  ok "data source $ds_id"

  aws bedrock-agent start-ingestion-job \
    --knowledge-base-id "$kb_id" --data-source-id "$ds_id" \
    --region "$REGION" >/dev/null 2>&1 || {
      warn "could not start the ingestion job for ${domain}"
      record "KB ${domain}" "PARTIAL" "ingestion not started"
      env_set "$var" "$kb_id"
      return 1
    }

  # Poll to COMPLETE — queries return nothing until the sync finishes.
  # A terminal FAILED/STOPPED job will never become COMPLETE, so exit as soon
  # as one is seen instead of polling the full 600s three times over (up to
  # 30 minutes burned on a time-limited Cloud Lab session for nothing).
  local status="" waited=0
  while [[ "$status" != "COMPLETE" && "$status" != "FAILED" \
           && "$status" != "STOPPED" && $waited -lt 600 ]]; do
    sleep 15; waited=$((waited+15))
    status=$(aws bedrock-agent list-ingestion-jobs \
      --knowledge-base-id "$kb_id" --data-source-id "$ds_id" \
      --region "$REGION" \
      --query 'ingestionJobSummaries[0].status' --output text 2>/dev/null)
    printf '\r   syncing %s … %s (%ds)' "$domain" "$status" "$waited"
  done
  printf '\n'

  if [[ "$status" == "COMPLETE" ]]; then
    ok "KB ${domain} synced"
    record "KB ${domain}" "OK" "$kb_id"
  elif [[ "$status" == "FAILED" || "$status" == "STOPPED" ]]; then
    bad "KB ${domain} ingestion ended as ${status} — see the Bedrock console for the job's failure reasons"
    record "KB ${domain}" "PARTIAL" "$kb_id (sync: ${status})"
  else
    warn "KB ${domain} sync ended as ${status:-UNKNOWN}"
    record "KB ${domain}" "PARTIAL" "$kb_id (sync: ${status:-UNKNOWN})"
  fi

  env_set "$var" "$kb_id"
}

ensure_kbs() {
  phase "Bedrock Knowledge Bases (returns, shipping, warranty)"
  ensure_vector_infra || true
  create_kb returns  returns-policy-index  RETURNS_KB_ID
  create_kb shipping shipping-policy-index SHIPPING_KB_ID
  create_kb warranty warranty-policy-index WARRANTY_KB_ID
}

# ═════════════════════════════════════════════════════════════════════════════
#  6. Deploy the agent
# ═════════════════════════════════════════════════════════════════════════════
# src/agent_orchestrator.py deploy runs the whole agent-side pipeline in one
# call: builds the 5-agent graph, creates the enterprise guardrail, deploys
# to AgentCore Runtime, configures Memory and CloudWatch/X-Ray observability,
# and (best-effort) the AgentCore Gateway — then prints the three lines this
# function parses back out.
deploy_agent_phase() {
  phase "Deploying the agent (src/agent_orchestrator.py deploy)"

  local out
  out="$( cd "$PROJECT_DIR" && \
    AWS_REGION="$REGION" PROJECT_NAME="$PROJECT_NAME" \
    RETURNS_KB_ID="$(load kb_returns)" SHIPPING_KB_ID="$(load kb_shipping)" \
    WARRANTY_KB_ID="$(load kb_warranty)" \
    "$PY" src/agent_orchestrator.py deploy 2>&1 )"

  mkdir -p "$EVIDENCE_DIR"
  printf '%s\n' "$out" > "${EVIDENCE_DIR}/deploy_output.txt"

  local runtime_arn guardrail_id guardrail_version
  runtime_arn="$(grep -oE 'AGENTCORE_RUNTIME_ARN=.*' <<<"$out" | tail -1 | cut -d= -f2-)"
  guardrail_id="$(grep -oE '^ *GUARDRAIL_ID=.*' <<<"$out" | tail -1 | sed 's/.*GUARDRAIL_ID=//')"
  guardrail_version="$(grep -oE '^ *GUARDRAIL_VERSION=.*' <<<"$out" | tail -1 | sed 's/.*GUARDRAIL_VERSION=//')"

  if [[ -z "$runtime_arn" ]]; then
    bad "deploy did not report a runtime ARN — see ${EVIDENCE_DIR}/deploy_output.txt"
    tail -20 <<<"$out" | sed 's/^/       /'
    cat <<EOF

   ${BOLD}Finish by hand instead:${RESET}
     cd $PROJECT_DIR && $PY src/agent_orchestrator.py deploy
     Then set AGENTCORE_RUNTIME_ARN / GUARDRAIL_ID / GUARDRAIL_VERSION in $ENV_FILE

EOF
    record "Agent deploy" "FAILED" "see ${EVIDENCE_DIR}/deploy_output.txt"
    return 1
  fi

  save runtime_arn "$runtime_arn"
  save guardrail_id "$guardrail_id"
  save guardrail_version "$guardrail_version"
  env_set AGENTCORE_RUNTIME_ARN "$runtime_arn"
  env_set GUARDRAIL_ID "$guardrail_id"
  env_set GUARDRAIL_VERSION "${guardrail_version:-DRAFT}"

  ok "runtime $runtime_arn"
  ok "guardrail $guardrail_id (${guardrail_version:-DRAFT})"
  record "Agent deploy" "OK" "$runtime_arn"
}

# ═════════════════════════════════════════════════════════════════════════════
#  7. Run the Udacity grader
# ═════════════════════════════════════════════════════════════════════════════
# Prefers a workspace-provided tests/test_agent.py (e.g. from an earlier
# upload into the CloudShell home directory) over the embedded copy, and
# prints which one was used — the grader is the thing being graded, so which
# copy ran matters.
run_grader() {
  phase "Running the Udacity grader (tests/test_agent.py all)"
  mkdir -p "$EVIDENCE_DIR"

  local workspace_copy="${PWD}/tests/test_agent.py"
  if [[ -f "$workspace_copy" && "$workspace_copy" != "${PROJECT_DIR}/tests/test_agent.py" ]]; then
    cp "$workspace_copy" "${PROJECT_DIR}/tests/test_agent.py"
    ok "using the workspace-provided tests/test_agent.py ($workspace_copy)"
  else
    ok "using the embedded tests/test_agent.py"
  fi

  ( cd "$PROJECT_DIR" && \
    set -a; [[ -f "$ENV_FILE" ]] && source "$ENV_FILE"; set +a; \
    AWS_REGION="$REGION" PROJECT_NAME="$PROJECT_NAME" \
    "$PY" tests/test_agent.py all ) 2>&1 | tee "${EVIDENCE_DIR}/pytest_output.txt"
  local rc=${PIPESTATUS[0]}

  # print_score() in test_agent.py always exits 0 on a real run (it never
  # calls sys.exit on a partial score), so a clean exit code alone does not
  # mean the grade was good — the actual "Score: X/Y" line is what to trust.
  #
  # test_agent.py wraps that line in ANSI colour codes (Colors.BOLD before
  # "Score:" and a colour code between "Score: " and the digits), so the
  # digits are never actually adjacent to the literal text "Score: " in the
  # raw bytes - a plain grep on the file as captured misses every run,
  # including a perfect one, and the summary below would print PARTIAL/"no
  # score line found" right next to a genuine 120/120. Strip ANSI escapes
  # before matching.
  local score_line
  score_line="$(sed -r 's/\x1B\[[0-9;]*[mK]//g' "${EVIDENCE_DIR}/pytest_output.txt" 2>/dev/null \
    | grep -oE 'Score: [0-9]+/[0-9]+ pts \([0-9]+%\)' | tail -1)"

  if [[ $rc -ne 0 ]]; then
    bad "grader crashed (exit $rc) — see ${EVIDENCE_DIR}/pytest_output.txt"
    record "Grader (test_agent.py all)" "FAILED" "exit $rc"
  elif [[ -n "$score_line" ]]; then
    ok "grader finished — $score_line"
    record "Grader (test_agent.py all)" "OK" "$score_line"
  else
    warn "grader finished but no score line found — check ${EVIDENCE_DIR}/pytest_output.txt"
    record "Grader (test_agent.py all)" "PARTIAL" "no score line found"
  fi
}

# ═════════════════════════════════════════════════════════════════════════════
#  8. Adversarial guardrail suite
# ═════════════════════════════════════════════════════════════════════════════
run_adversarial_phase() {
  phase "Adversarial guardrail suite"
  local script="${PROJECT_DIR}/scripts/run_adversarial.py"

  if [[ ! -f "$script" ]]; then
    skip "scripts/run_adversarial.py not present — skipping"
    record "Adversarial suite" "SKIPPED" "script not found"
    return 0
  fi

  ( cd "$PROJECT_DIR" && "$PY" scripts/run_adversarial.py --live ) \
    2>&1 | tee "${EVIDENCE_DIR}/adversarial_output.txt"
  local rc=${PIPESTATUS[0]}
  if [[ $rc -eq 0 ]]; then
    ok "adversarial suite finished — see ${EVIDENCE_DIR}/adversarial_output.txt"
    record "Adversarial suite" "OK" "${EVIDENCE_DIR}/adversarial_output.txt"
  else
    warn "adversarial suite reported failures (exit $rc)"
    record "Adversarial suite" "PARTIAL" "${EVIDENCE_DIR}/adversarial_output.txt"
  fi
}

# ═════════════════════════════════════════════════════════════════════════════
#  9. Scenario transcripts (Task 14)
# ═════════════════════════════════════════════════════════════════════════════
# scripts/run_scenarios.py runs the three Udacity-brief scenarios against the
# just-deployed runtime and writes one transcript each, plus an X-Ray trace
# lookup per scenario. It needs a real runtime_arn — if deploy_agent_phase
# never ran (e.g. a bare --package on an undeployed project), this skips
# rather than failing the whole run.
run_scenarios_phase() {
  phase "Scenario transcripts (scripts/run_scenarios.py)"
  local script="${PROJECT_DIR}/scripts/run_scenarios.py"

  if [[ ! -f "$script" ]]; then
    skip "scripts/run_scenarios.py not present — skipping"
    record "Scenario transcripts" "SKIPPED" "script not found"
    return 0
  fi
  if [[ -z "$(load runtime_arn)" ]] && ! grep -q '^AGENTCORE_RUNTIME_ARN=.' "$ENV_FILE" 2>/dev/null; then
    skip "no deployed runtime yet — run a full deploy first"
    record "Scenario transcripts" "SKIPPED" "no runtime_arn"
    return 0
  fi

  ( cd "$PROJECT_DIR" && \
    set -a; [[ -f "$ENV_FILE" ]] && source "$ENV_FILE"; set +a; \
    "$PY" scripts/run_scenarios.py --live --run-name live ) \
    2>&1 | tee "${EVIDENCE_DIR}/scenarios_output.txt"
  local rc=${PIPESTATUS[0]}
  if [[ $rc -eq 0 ]]; then
    ok "scenario transcripts written to ${EVIDENCE_DIR}/scenarios"
    record "Scenario transcripts" "OK" "${EVIDENCE_DIR}/scenarios"
  else
    warn "scenario run reported at least one failure (exit $rc)"
    record "Scenario transcripts" "PARTIAL" "${EVIDENCE_DIR}/scenarios_output.txt"
  fi
}

# ═════════════════════════════════════════════════════════════════════════════
#  10. Package the submission
# ═════════════════════════════════════════════════════════════════════════════
# Produces novamart-submission.zip: src/agent_orchestrator.py, .env with every
# value redacted (key names kept), both required screenshots
# (01-test-score.png, 02-xray-service-map.png) if present, the adversarial
# suite, the scenario transcripts, and an INDEX.md that says plainly which
# parts are live and which are still missing.
#
# Screenshots are captured locally by scripts/capture_console.py (it drives a
# real signed-in Chrome session — CloudShell has no GUI for that), so this
# phase looks for them at ${EVIDENCE_DIR}/screenshots/*.png. Upload that
# directory into CloudShell (Actions → Upload file) before running --package
# if you captured them on a laptop rather than in this same CloudShell home.
package_submission() {
  phase "Packaging the submission"

  local staging="/tmp/novamart-submission" out="${HOME}/novamart-submission.zip"
  rm -rf "$staging" "$out"
  mkdir -p "$staging/src" "$staging/screenshots" "$staging/adversarial" "$staging/scenarios"

  # ── src/agent_orchestrator.py (explicitly required by the rubric) ─────────
  if [[ -f "$PROJECT_DIR/src/agent_orchestrator.py" ]]; then
    cp "$PROJECT_DIR/src/agent_orchestrator.py" "$staging/src/"
    ok "src/agent_orchestrator.py"
  else
    warn "src/agent_orchestrator.py not found in $PROJECT_DIR — materialise() may not have run"
  fi
  # The rest of src/, tests/ and infrastructure/ too — more context for a
  # reviewer costs nothing and the rubric's minimum list is a floor, not a
  # ceiling.
  cp -r "$PROJECT_DIR/tests" "$staging/" 2>/dev/null
  cp -r "$PROJECT_DIR/infrastructure" "$staging/" 2>/dev/null
  cp -r "$PROJECT_DIR/src" "$staging/src_full" 2>/dev/null

  # ── .env — populated, with only true credentials redacted ──────────────────
  # The project brief asks for "the populated .env", and the rubric checks
  # that it "contains valid, non-empty values for RETURNS_KB_ID,
  # SHIPPING_KB_ID, and WARRANTY_KB_ID". Blanket redaction fails that outright.
  # The values this file holds are resource identifiers, not secrets: KB ids,
  # a runtime ARN, a guardrail id. Those ship as-is. Anything credential-shaped
  # is redacted, so a stray access key pasted into .env never reaches the zip.
  if [[ -f "$ENV_FILE" ]]; then
    sed -E '/^(AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN)=/ s/=.*/=REDACTED/;
            /^[A-Za-z_][A-Za-z0-9_]*(SECRET|TOKEN|PASSWORD|PASSWD|PRIVATE_KEY)[A-Za-z0-9_]*=/ s/=.*/=REDACTED/' \
        "$ENV_FILE" > "$staging/.env"
    local kb_ids
    kb_ids="$(grep -cE '^(RETURNS|SHIPPING|WARRANTY)_KB_ID=.+' "$staging/.env" 2>/dev/null || echo 0)"
    if [[ "$kb_ids" -eq 3 ]]; then
      ok ".env (populated — 3 KB ids present, credentials redacted)"
    else
      warn ".env packaged but only ${kb_ids}/3 KB ids are populated — the rubric checks these"
    fi
  else
    warn "no .env found at $ENV_FILE — the submission needs the populated .env"
  fi

  # ── screenshots — captured locally, not by this script ─────────────────────
  local shots_src="${EVIDENCE_DIR}/screenshots"
  local shots_found=0
  if [[ -d "$shots_src" ]]; then
    cp "$shots_src"/*.png "$staging/screenshots/" 2>/dev/null
    shots_found=$(find "$staging/screenshots" -name '*.png' 2>/dev/null | wc -l | tr -d ' ')
  fi
  if [[ -f "$staging/screenshots/01-test-score.png" && -f "$staging/screenshots/02-xray-service-map.png" ]]; then
    ok "both required screenshots present ($shots_found total)"
    record "Screenshots" "OK" "$shots_found found, both required present"
  elif [[ "$shots_found" -gt 0 ]]; then
    warn "only $shots_found screenshot(s) found — the two REQUIRED shots are"
    warn "01-test-score.png and 02-xray-service-map.png. Run scripts/capture_console.py"
    warn "locally and copy its output into ${shots_src}, then re-run --package."
    record "Screenshots" "PARTIAL" "$shots_found found, required ones missing"
  else
    warn "no screenshots found at $shots_src"
    warn "run scripts/capture_console.py locally, then copy its output here."
    record "Screenshots" "MISSING" "run scripts/capture_console.py, see cloudshell/README.md"
  fi

  # ── adversarial suite + scenario transcripts, whichever ran ────────────────
  [[ -d "${EVIDENCE_DIR}/adversarial" ]] && cp -r "${EVIDENCE_DIR}/adversarial"/. "$staging/adversarial/" 2>/dev/null
  [[ -d "${EVIDENCE_DIR}/scenarios" ]]   && cp -r "${EVIDENCE_DIR}/scenarios"/.   "$staging/scenarios/"   2>/dev/null
  local adv_count=$(find "$staging/adversarial" -type f 2>/dev/null | wc -l | tr -d ' ')
  local scen_count=$(find "$staging/scenarios" -type f 2>/dev/null | wc -l | tr -d ' ')
  [[ "$adv_count"  -gt 0 ]] && ok "adversarial suite ($adv_count files)"  || warn "no adversarial evidence found at ${EVIDENCE_DIR}/adversarial"
  [[ "$scen_count" -gt 0 ]] && ok "scenario transcripts ($scen_count files)" || warn "no scenario transcripts found at ${EVIDENCE_DIR}/scenarios"

  # ── DEPLOYED_RESOURCES.txt — kept for a quick human-readable summary ──────
  {
    printf 'NovaMart submission — packaged %s by deploy-e2e %s\n\n' \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SCRIPT_VERSION"
    printf 'Deployed resources — account %s, %s\n' "$(load account)" "$REGION"
    printf '  Runtime ARN     %s\n' "$(load runtime_arn)"
    printf '  Guardrail       %s (%s)\n' "$(load guardrail_id)" "$(load guardrail_version)"
    printf '  Returns KB      %s\n' "$(load kb_returns)"
    printf '  Shipping KB     %s\n' "$(load kb_shipping)"
    printf '  Warranty KB     %s\n' "$(load kb_warranty)"
  } > "$staging/DEPLOYED_RESOURCES.txt"

  # ── INDEX.md — states plainly what is here and what is missing ────────────
  {
    printf '# NovaMart submission index\n\n'
    printf 'Packaged %s by deploy-e2e %s.\n\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SCRIPT_VERSION"
    printf '## Contents\n\n'
    printf '| Item | Status |\n|---|---|\n'
    if [[ -f "$staging/src/agent_orchestrator.py" ]]; then
      printf '| src/agent_orchestrator.py | present |\n'
    else
      printf '| src/agent_orchestrator.py | MISSING |\n'
    fi
    if [[ -f "$staging/.env" ]]; then
      printf '| .env (populated) | KB ids, runtime ARN and guardrail id intact; credential-shaped keys redacted |\n'
    else
      printf '| .env (redacted) | MISSING |\n'
    fi
    if [[ -f "$staging/screenshots/01-test-score.png" ]]; then
      printf '| screenshots/01-test-score.png (required) | present |\n'
    else
      printf '| screenshots/01-test-score.png (required) | MISSING |\n'
    fi
    if [[ -f "$staging/screenshots/02-xray-service-map.png" ]]; then
      printf '| screenshots/02-xray-service-map.png (required) | present |\n'
    else
      printf '| screenshots/02-xray-service-map.png (required) | MISSING |\n'
    fi
    printf '| screenshots/ (supporting, 03-06) | %s file(s) |\n' "$shots_found"
    printf '| adversarial/ | %s file(s) |\n' "$adv_count"
    printf '| scenarios/ | %s file(s) |\n' "$scen_count"
    printf '\n## Honesty note\n\n'
    printf 'This zip was assembled by an automated script. A "present" row above\n'
    printf 'means the file existed on disk when packaged — it does not by itself\n'
    printf 'prove the screenshot shows what its filename claims. Open every\n'
    printf 'screenshot before submitting. A MISSING row for either required\n'
    printf 'screenshot means the submission is not yet complete: run\n'
    printf '`scripts/capture_console.py` locally against the signed-in AWS console\n'
    printf 'and copy its output into `%s` before re-running --package.\n' "$shots_src"
  } > "$staging/INDEX.md"

  if command -v zip >/dev/null 2>&1; then
    ( cd "$staging" && zip -qr "$out" . )
    ok "$out ($(du -h "$out" 2>/dev/null | cut -f1))"
    printf '\n   %sDownload it:%s CloudShell → Actions → Download file → paste this exact path:\n' "$BOLD" "$RESET"
    printf '     %s\n\n' "$out"
    record "Package" "OK" "$out"
  else
    bad "zip not found — cannot package. The staged files are at $staging"
    record "Package" "FAILED" "zip not found"
  fi
}

# ═════════════════════════════════════════════════════════════════════════════
#  Summary, status, teardown
# ═════════════════════════════════════════════════════════════════════════════
summary() {
  printf '\n%s════════════════════════════════════════════════════════════════════%s\n' "$BOLD" "$RESET"
  printf '%s SUMMARY%s\n' "$BOLD" "$RESET"
  printf '%s════════════════════════════════════════════════════════════════════%s\n\n' "$BOLD" "$RESET"

  local entry name status detail colour
  for entry in "${RESULTS[@]}"; do
    IFS='|' read -r name status detail <<<"$entry"
    case "$status" in
      OK)       colour="$GREEN" ;;
      PARTIAL)  colour="$YELLOW" ;;
      SKIPPED)  colour="$DIM" ;;
      *)        colour="$RED" ;;
    esac
    printf '  %-28s %s%-9s%s %s\n' "$name" "$colour" "$status" "$RESET" "$detail"
  done

  cat <<EOF

  Project       $PROJECT_DIR
  State         $STATE_DIR
  Evidence      $EVIDENCE_DIR

${RED}${BOLD}  ┌──────────────────────────────────────────────────────────────┐
  │  TEAR DOWN WHEN YOU HAVE YOUR SCREENSHOTS                    │
  │                                                              │
  │     bash ${BASH_SOURCE[0]} --teardown
  │                                                              │
  │  Bedrock Knowledge Base storage and its S3 Vectors index      │
  │  bill while idle, whether or not anything queries them.      │
  └──────────────────────────────────────────────────────────────┘${RESET}

  This table reports what this run actually did, call by call. It is the
  authority — prefer it over the banner at the top, which describes what
  previous runs established rather than this one.

EOF
}

show_status() {
  printf '\n%sRecorded state%s\n\n' "$BOLD" "$RESET"
  local key
  for key in account caller_arn \
             out_PolicyDocumentsBucketName out_VectorStoreBucketName out_AgentCoreRoleArn \
             policy_bucket vector_bucket vector_bucket_arn agentcore_role_arn \
             kb_returns kb_shipping kb_warranty \
             ds_returns ds_shipping ds_warranty \
             runtime_arn guardrail_id guardrail_version; do
    printf '  %-28s %s\n' "$key" "$(load "$key")"
  done
  printf '\n'
}

# Delegates every deletion to infrastructure/cleanup.py — never reimplements
# it here — and then removes only what this script itself created locally.
# cloudshell/cleanup-all.sh does the same thing for a full git checkout.
teardown() {
  printf '\n%sTeardown%s — deletes everything this project created.\n' "$BOLD" "$RESET"
  printf 'Type %sdelete%s to confirm: ' "$BOLD" "$RESET"
  read -r reply
  [[ "$reply" == "delete" ]] || { warn "cancelled"; return; }

  if [[ ! -f "$PROJECT_DIR/infrastructure/cleanup.py" ]]; then
    bad "no materialised project at $PROJECT_DIR — nothing to delete. Run the script once first."
    return 1
  fi

  # Re-resolve PROJECT_NAME from the embedded config.py, exactly like every
  # other phase does via materialise(). Skipping this left PROJECT_NAME set
  # to the empty string (from main()'s placeholder), and config.py's
  # os.environ.get('PROJECT_NAME', 'udacity-agentcore') treats an exported-
  # but-empty env var as a real value rather than falling back - so
  # _owned() matched every resource in the account (fail-"safe" only by
  # accident) while _guard_account() called describe-stacks with an empty
  # stack name and sys.exit(3)'d before anything was deleted. materialise()
  # also re-writes the project files, which is cheap and idempotent.
  materialise

  # cleanup.py does `import config`, which unconditionally does
  # `from dotenv import load_dotenv` — it needs the same venv every other
  # phase does. install_dependencies() is cheap to re-run: it no-ops
  # (skip) if $VENV_DIR already satisfies the import check from an earlier
  # run in this same $HOME.
  install_dependencies || warn "continuing with system python3 — cleanup.py may fail to import config"

  ( cd "$PROJECT_DIR" && AWS_REGION="$REGION" PROJECT_NAME="$PROJECT_NAME" \
      "$PY" infrastructure/cleanup.py --yes )
  local rc=$?

  # Preserve the evidence this project produced BEFORE anything is removed.
  # Every doc here tells the user to screenshot/package evidence/live and
  # then immediately tear down to stop the billing meter - deleting
  # PROJECT_DIR must not also delete the proof of what ran.
  if [[ -d "$EVIDENCE_DIR" ]] && [[ -n "$(ls -A "$EVIDENCE_DIR" 2>/dev/null)" ]]; then
    local archive="${HOME}/novamart-evidence-$(date -u +%Y%m%dT%H%M%SZ)"
    mkdir -p "$archive"
    cp -r "$EVIDENCE_DIR"/. "$archive"/ 2>/dev/null \
      && printf '%sEvidence preserved:%s %s\n' "$GREEN" "$RESET" "$archive" \
      || warn "could not copy evidence out of $EVIDENCE_DIR before teardown"
  fi

  if [[ $rc -eq 0 ]]; then
    rm -rf "$STATE_DIR" "$PROJECT_DIR"
    printf '\n%sLocal state removed:%s %s, %s\n' "$GREEN" "$RESET" "$STATE_DIR" "$PROJECT_DIR"
    printf '%sDone.%s Verify in the console that the Knowledge Bases and S3 Vectors bucket\n' "$GREEN" "$RESET"
    printf 'are gone — those are what bill while idle.\n\n'
  else
    printf '%scleanup.py reported at least one failure — check its summary above and\n' "$YELLOW"
    printf 'finish any remaining deletions in the AWS console.%s\n' "$RESET"
    printf 'Local state was left in place at %s and %s so you can retry:\n' "$STATE_DIR" "$PROJECT_DIR"
    printf '  bash %s --teardown\n\n' "${BASH_SOURCE[0]}"
  fi
}

# ═════════════════════════════════════════════════════════════════════════════
main() {
  # Placeholders until materialise() reads the real values out of config.py.
  PROJECT_NAME="${PROJECT_NAME:-}"
  STACK_NAME="${PROJECT_NAME:-udacity-agentcore}"
  ORCH_MODEL_ID=""
  WORKER_MODEL_ID_VAL=""

  case "${1:-}" in
    --status)    show_status; exit 0 ;;
    --teardown)  teardown;    exit 0 ;;
    --test-only)
      banner
      materialise
      install_dependencies
      preflight
      run_grader
      summary
      exit 0 ;;
    --package)
      banner
      materialise
      install_dependencies || warn "continuing without a verified venv — run_scenarios_phase may fail to import config"
      run_scenarios_phase
      package_submission
      summary
      exit 0 ;;
  esac

  banner
  materialise
  install_dependencies
  preflight
  deploy_stack
  seed_data_phase
  ensure_kbs
  deploy_agent_phase
  run_grader
  run_adversarial_phase
  run_scenarios_phase
  package_submission
  summary
}

main "$@"
