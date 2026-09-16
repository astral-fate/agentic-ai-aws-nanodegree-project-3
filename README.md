# NovaMart multi-agent customer support

Udacity "Agentic AI on AWS" nanodegree, project 3: a multi-agent customer
support system built with the Strands Agents SDK and deployed to Amazon
Bedrock AgentCore.

**Status: everything is built and offline-tested (61/61 passing). Nothing
has been run against a live AWS account yet** — no credentials are
available on this machine and the Udacity Cloud Lab had not been launched
at the time of this commit. See
[`docs/TESTING.md`](docs/TESTING.md) for exactly what that does and does
not mean for the claims below.

## The agent graph

```
                         Customer Request
                                │
                     OrchestratorAgent  (Claude Haiku, temp 0.0)
                     routes, owns WorkflowState, never answers directly
                                │
        ┌───────────┬──────────┼──────────┬───────────────┐
        │           │          │          │               │
 InventoryAgent  RefundAgent  PolicyAgent  │       CommunicationAgent
  3 tools         2 tools     1 tool       │           1 tool
  DynamoDB        eligibility  fan-out ────┤           composes the
  order/customer  windows: 30d │           │           final reply
  facts           Std/60d Prem │           │
                          ┌─────┴───┐
              ReturnsPolicyRetrieverAgent  ShippingPolicyRetrieverAgent  WarrantyPolicyRetrieverAgent
                (KB: returns)                 (KB: shipping)                (KB: warranty)
                └──────────────── all three run in PARALLEL ────────────────┘
```

Full explanation of the graph, the `WorkflowState` version lifecycle, and
why the policy fan-out is three real sub-agents rather than three plain
tool calls: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Quickstart

### Path A — offline harness (no AWS account needed)

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests_offline/ -v      # 61 passed in 18.07s (evidence/run-01/pytest_output.txt)
```

This imports the real, unmodified `src/agent_orchestrator.py` through
`harness/bootstrap.py`, which registers `moto` DynamoDB/CloudFormation and a
scripted, rule-based model stand-in in `sys.modules` before the import
happens. The graded file is never edited or copied to test it. See
[`docs/TESTING.md`](docs/TESTING.md) for exactly what this suite proves —
and, just as importantly, what it does not.

### Path B — one-paste AWS CloudShell deploy (live, costs money while up)

```bash
bash cloudshell/deploy-e2e-v02.sh
```

Deploys the full stack (DynamoDB, S3, S3 Vectors + 3 Knowledge Bases, the
AgentCore Runtime with a guardrail, memory and observability configured),
runs the grader, and writes evidence to `evidence/live/`. Full
phase-by-phase runbook, including screenshotting, packaging and teardown:
[`docs/RUNBOOK.md`](docs/RUNBOOK.md). **This path bills while the Knowledge
Base storage and its S3 Vectors index sit idle — tear down as soon as you
have what you need.**

## What has and has not been run live

**Not yet run:** anything in Path B. `evidence/live/` does not exist as of
this commit — see `evidence/README.md`. The two screenshots the rubric
requires (`01-test-score.png` showing `120/120`, `02-xray-service-map.png`
showing the Orchestrator → Worker → KnowledgeBase chain) are **pending**.

**Run and committed:** the full offline suite
(`evidence/run-01/pytest_output.txt`, 61/61), the three Udacity-brief
scenarios through the real five-agent graph
(`evidence/offline/scenarios/`), and the guardrail-configuration coverage
check for six adversarial prompts (`evidence/offline/adversarial/`). All
three are real runs of real, unmodified project code — against a stubbed
AWS account and a rule-based routing stand-in, never against a real model.

## Corrections to the Udacity brief

Three things the brief gets wrong that a reader following it literally
would trip over — verified directly against the starter files, detailed in
[`docs/TESTING.md`](docs/TESTING.md):

1. `tests/test_agent.py` has no `test_5_parallel_retrieval`; parallel RAG
   is graded by the written rubric and this project's own harness, not by
   the automated grader.
2. `infrastructure/starter_stack.yaml` does not create an S3 Vectors
   bucket or vector indexes — it creates three DynamoDB tables, two plain
   S3 buckets, an IAM role and a log group. The deploy script provisions
   the real vector infrastructure itself.
3. `config.py` ships in two variants with different model IDs (Claude 4.5
   vs. OpenAI `gpt-oss`). This project references only the config
   constants, so either variant passes.

## Documentation index

| Doc | What it covers |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | The agent graph, `WorkflowState` version lifecycle, why Policy fans out to three sub-agents |
| [`docs/TESTING.md`](docs/TESTING.md) | The proven/not-proven split — read this before citing any test result as evidence |
| [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | Deploy, test, screenshot, package, teardown, and what to do when each phase fails |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Guardrail policies, PII handling, secrets handling |
| [`SUBMISSION.md`](SUBMISSION.md) | Rubric-item-to-evidence table |
| [`REFLECTION.md`](REFLECTION.md) | Written reflection |
| [`STARTER_PROVENANCE.md`](STARTER_PROVENANCE.md) | Which files are untouched Udacity starter code vs. authored here, and how each was verified |
| [`evidence/README.md`](evidence/README.md) | What every evidence directory does and does not prove |
| [`cloudshell/README.md`](cloudshell/README.md) | What the one-paste deploy script does, phase by phase |

## Repository layout

| Path | What it is |
|---|---|
| `src/agent_orchestrator.py` | The graded deliverable — the five agent builders, guardrail, deployment, memory and observability configuration. The one file we authored ourselves; every other `src/` file is untouched starter code (see `STARTER_PROVENANCE.md`) |
| `config.py`, `infrastructure/` | Untouched starter files (resource config, CloudFormation stack, seed data) |
| `harness/` | Our offline stand-ins: `moto`/CloudFormation bootstrap, fake AWS clients, a scripted model, KB fixtures |
| `tests_offline/` | 61 tests against the harness-driven graph |
| `tests/test_agent.py` | The untouched 120-point Udacity grader — runs only against a live account |
| `scripts/` | Scenario runner, adversarial suite, CloudShell script generator, console screenshot capture |
| `cloudshell/` | The generated one-paste deploy script and its template |
| `evidence/` | Committed proof for every claim this project makes, offline and (eventually) live |
