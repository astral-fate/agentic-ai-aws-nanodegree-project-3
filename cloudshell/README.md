# Deploying from AWS CloudShell

CloudShell already has the AWS CLI, credentials, Python and `jq`, which is why
the live path runs there rather than on a laptop.

```bash
bash cloudshell/deploy-e2e-v02.sh
```

Paste the whole file directly into a CloudShell terminal, or upload it with
CloudShell's Actions → Upload file, then run it. It is self-contained: every
project file it needs (`config.py`, the `src/` modules, `tests/test_agent.py`,
the `infrastructure/` scripts) is embedded inside it, so no `git clone` and no
other download is required beyond calls to AWS itself.

```bash
bash cloudshell/deploy-e2e-v02.sh --status     # what exists, change nothing
bash cloudshell/deploy-e2e-v02.sh --test-only   # re-run the grader only
bash cloudshell/deploy-e2e-v02.sh --package     # zip src/ + evidence for submission
bash cloudshell/deploy-e2e-v02.sh --teardown    # delete what the script created
```

## Honesty note

This script was written and syntax-checked (`bash -n`), but as of this
commit it has **not** been executed against a live AWS account — no
credentials with the necessary permissions were available, and the Udacity
Cloud Lab had not been launched. Every AWS-mutating call inside it is
therefore treated as fallible: a failure prints the exact console steps for
that one piece and the script continues with the rest, rather than claiming
success it cannot verify. The summary table printed at the end of a run is
the source of truth for what actually happened — trust it over the banner.

## What it does

Deterministically, without asking, in order:

1. **Materialise** — writes every embedded project file into
   `~/novamart-project`.
2. **Install dependencies** — CloudShell's python3 ships `boto3` but not
   `strands-agents` or `python-dotenv`, both of which `config.py` and
   `src/agent_orchestrator.py` import unconditionally. This step creates a
   dedicated virtualenv at `~/.novamart-venv` and installs
   `requirements.txt` into it — not `pip install --user`, because
   CloudShell's own python3 is itself already inside a virtualenv, where
   `--user` installs fail outright. Every later phase runs project code
   through that venv's python3, and the install is verified by actually
   importing `strands` and `dotenv` afterward, not assumed from pip's exit
   code. Nothing needs installing by hand first.
3. **Preflight** — identity, region, whether the orchestrator/worker
   foundation models are visible, then a read-only permission probe that
   reports **every** missing permission at once, before the first write:

   ```
   [fail] This identity cannot deploy the project. Missing:

       s3vectors:ListVectorBuckets                the Knowledge Base vector store
       bedrock-agent:ListKnowledgeBases            the three policy KBs

   Nothing has been created — this check runs before the first write.
   ```
4. **CloudFormation** — deploys `infrastructure/starter_stack.yaml` as stack
   `udacity-agentcore` (DynamoDB tables, the policy/vector S3 buckets, the
   AgentCore IAM role, the CloudWatch log group), then reads its outputs.
5. **Seed data** — `infrastructure/seed_data.py`: customers, orders, and the
   three policy documents uploaded to S3.
6. **Knowledge Bases ×3** — the phase with no precedent in the sibling
   project. The CloudFormation stack provisions a plain S3 bucket for vector
   storage but not a native S3 Vectors vector-bucket or its indexes (that is
   a separate resource type outside CloudFormation's coverage here), so this
   step first creates the vector bucket and the three
   `{returns,shipping,warranty}-policy-index` indexes via the `s3vectors` CLI,
   then creates one Bedrock Knowledge Base per domain
   (`novamart-{domain}-policy-kb`), its S3 data source scoped to
   `policies/{domain}/`, and starts and polls the ingestion job to
   `COMPLETE`.
7. **Deploy the agent** — `src/agent_orchestrator.py deploy`, which itself
   builds the 5-agent graph, creates the enterprise guardrail, deploys to
   AgentCore Runtime, and configures Memory and CloudWatch/X-Ray
   observability. The runtime ARN, guardrail id and guardrail version are
   parsed out of its output and written to `.env`.
8. **Grade** — prefers a workspace-provided `tests/test_agent.py` (copied in
   before the run) over the embedded copy, prints which one it used, then
   runs `tests/test_agent.py all` (through the venv's python3) and tees the
   output to `evidence/live/pytest_output.txt`. The grader's own exit code
   is always 0 on a completed run, so the summary reads the printed
   `Score: X/Y` line rather than trusting the exit code alone.
9. **Adversarial suite** — `scripts/run_adversarial.py --live`: six hostile
   prompts sent through the deployed runtime, transcripts written to
   `evidence/live/adversarial/`.
10. **Scenario transcripts** — `scripts/run_scenarios.py --live`: the three
    Udacity-brief scenarios (return an order, ask a policy question, do the
    discount math) sent through the deployed runtime, one transcript each in
    `evidence/live/scenarios/`, each with an X-Ray trace-id lookup for that
    call's time window.
11. **Package** — `~/novamart-submission.zip`: `src/agent_orchestrator.py`
    (plus the rest of `src/`, `tests/` and `infrastructure/` for context),
    `.env` with every value redacted to `REDACTED` (key names kept), the
    adversarial transcripts, the scenario transcripts, whatever screenshots
    are staged at `evidence/live/screenshots/`, and an `INDEX.md` that says
    plainly which required screenshots are present and which are missing.
    Screenshots are **not** produced by this script — CloudShell has no GUI
    to drive a browser from — so run `scripts/capture_console.py` locally
    against a signed-in AWS console session and copy its output into
    `evidence/live/screenshots/` (upload it into this CloudShell session if
    you captured it elsewhere) before running `--package`, or re-run
    `--package` afterward to pick them up.

`--package` and `--test-only` both print the same honesty/cost banner and
end-of-run summary table as a full run — the banner and summary appear on
every mode that does real work, including the one you run last, right
before walking away from a still-billing account.

It is **resumable**. State is kept in `~/.novamart-state/`, and re-running
skips anything that already exists. Safe to paste again after a dropped
session.

Every AWS-mutating call has a console fallback: on failure it prints the
click-by-click console steps with the real bucket, index and role names
already filled in, and the run continues rather than aborting.

## What it does not do

- **Console screenshots.** `scripts/capture_console.py` drives a real,
  signed-in Chrome session and CloudShell has no GUI for that, so it is run
  locally, not by this script. `--package` looks for its output at
  `evidence/live/screenshots/` and says plainly in `INDEX.md` if the two
  required shots (`01-test-score.png`, `02-xray-service-map.png`) are
  missing, rather than packaging a submission that silently lacks them.

## Cost

| | |
|---|---|
| DynamoDB, S3 objects | effectively free at this volume |
| Bedrock model invocations | per token, scales with how much you test |
| **Bedrock Knowledge Base storage + its S3 Vectors index** | **bills while idle, whether or not anything queries it** |

The last row is the one that empties a student budget. Screenshot what you
need, then:

```bash
bash cloudshell/deploy-e2e-v02.sh --teardown
```

`--teardown` (and the equivalent `cloudshell/cleanup-all.sh`, for a full git
checkout) delegates every deletion to `infrastructure/cleanup.py --yes` —
knowledge bases and their data sources, the S3 Vectors bucket and indexes,
the AgentCore runtime and memory, the guardrail, the policy bucket's objects,
and finally the CloudFormation stack — and then removes only the local
artifacts the script itself created: `~/.novamart-state` and the staged
project copy at `~/novamart-project`.

## Reading it before running it

[`_deploy-e2e.template.sh`](_deploy-e2e.template.sh) is the source; the
generated `deploy-e2e-v02.sh` is what you actually paste, produced by
`python scripts/build_cloudshell_script.py`. The embedded files are quoted
heredocs (`<<'SENTINEL'`), not base64 — the whole thing is meant to stay
readable enough to check mid-run if something looks wrong. Every
AWS-mutating call is inside a function named for what it creates.
