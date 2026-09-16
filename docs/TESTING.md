# What the tests prove, and what they do not

The offline suite runs in about 11 seconds with no AWS account, no
credentials and no network:

```
python -m pytest tests_offline/ -v      # 61 passed
```

That speed is worth being suspicious of. A green run here means the code is
wired correctly — never that a real model, a real guardrail, or a real
Knowledge Base behaves the way this project needs it to. This document draws
that line explicitly, because it is the boundary every other claim in this
repository depends on.

## How the offline harness works

`harness/bootstrap.py` registers stand-ins in `sys.modules` — a `moto`
CloudFormation/DynamoDB stack, fake `bedrock`/`bedrock-agentcore` clients
(`harness/fakes.py`), and a scripted, rule-based model
(`harness/scripted_model.py`) — **before** `src/agent_orchestrator.py` is
imported. The graded file itself is never edited or copied to test it; the
harness intercepts what it imports. `harness/scripted_model.py` drives tool
calls by regex/keyword matching against the prompt, the way a very literal
reading of the routing system prompt would — it is not an LLM and makes no
judgment calls.

```
tests_offline/  ->  harness.bootstrap.load_orchestrator()
                       ├─ moto DynamoDB + CloudFormation exports   ← REAL library, fake AWS
                       ├─ fakes.FakeAgent / _StubClient            ← faked strands + boto3 clients
                       ├─ harness.scripted_model                   ← rule-based stand-in, NOT an LLM
                       └─ src/agent_orchestrator.py                ← REAL, unmodified, imported via sys.modules
```

## Proven offline

- **All five agents instantiate with the right models, temperatures and
  tool counts.** Orchestrator (`config.ORCHESTRATOR_MODEL_ID`, temp 0.0, 5
  tools), Inventory (`config.WORKER_MODEL_ID`, temp 0.1, 3 tools), Refund
  (temp 0.1, 2 tools), Policy coordinator (temp 0.2, 1 tool
  `search_all_policies`), Communication (temp 0.3, 1 tool). Every count is
  asserted by the exact tool-name set, not a bare integer
  (`tests_offline/test_orchestrator.py`, `test_inventory_agent.py`,
  `test_refund_agent.py`, `test_policy_agent.py`,
  `test_communication_agent.py`).
- **The three Udacity-brief scenarios route correctly, and the
  communication agent is always the last call.** Return request ->
  Inventory -> Refund; policy question -> Policy; math question -> no
  worker at all; account questions ("what is my tier?") go to Inventory and
  never to Policy
  (`test_orchestrator.py::test_return_request_routes_inventory_then_refund`,
  `::test_policy_question_routes_to_policy`,
  `::test_account_question_goes_to_inventory_never_policy`,
  `::test_math_question_routes_to_no_worker`,
  `::test_communication_is_always_the_last_call`). The same three scenarios
  are also run end-to-end through the real orchestrator and their tool-call
  sequences pinned in `tests_offline/test_scenarios.py`, with transcripts
  committed at `evidence/offline/scenarios/`.
- **Return-window boundaries hold exactly at the edge, not just in the
  middle.** 29 and 31 days for Standard (30-day window), 59 and 61 days for
  Premium (60-day window) — four cases, eligible on one side of each
  boundary and not the other
  (`test_refund_agent.py::test_return_window_boundaries`).
- **WorkflowState survives a stale-version writer with no lost column.** A
  conditional `update_item` with a deliberately stale `expected_version`
  fails, retries after a fresh read, and both writers' columns land with the
  version incremented correctly
  (`test_workflow_state.py::test_stale_version_writer_retries_and_neither_write_is_lost`).
  This uses a `threading.Lock` to make the conflict deterministic against
  `moto`'s DynamoDB mock, which does not reliably serialise concurrent
  conditional writes on its own — the test's own name and docstring say so.
  It demonstrates recovery from a stale version, not safety under genuinely
  concurrent writers.
- **The three Knowledge Base retrievers genuinely run in parallel, and one
  failing does not lose the other two.** A 200ms-per-call fixture proves
  the three calls overlap in wall-clock time rather than running serially
  (`test_policy_agent.py::test_retrievals_actually_overlap_in_time`); a
  forced failure on the shipping retriever still returns the returns and
  warranty results, with the failure recorded under `errors`
  (`::test_one_failing_retriever_does_not_lose_the_others`). A third test
  guards against silently reinstating an earlier bypass where
  `search_all_policies` called `retrieve_from_knowledge_base` directly
  instead of invoking the retriever sub-agents — this regression guard was
  verified to fail against that exact bypass before the fix landed
  (`::test_search_all_policies_actually_invokes_each_retriever_agent`).
- **The guardrail request carries every required policy and is versioned,
  not `DRAFT`.** The payload built by `create_guardrail()` is checked for
  its content filters, PII entity actions, denied topics and the profanity
  word list, and the returned version is asserted to be a real published
  number rather than the unpublished default
  (`test_guardrail.py::test_guardrail_request_has_every_required_policy`,
  `::test_guardrail_is_versioned_not_draft`).
- **The runtime request uses `PUBLIC`/`HTTP` with all eight environment
  variables.** `networkConfiguration.networkMode == 'PUBLIC'`,
  `protocolConfiguration.serverProtocol == 'HTTP'`, and
  `environmentVariables` carries `AWS_REGION`, `PROJECT_NAME`,
  `RETURNS_KB_ID`, `SHIPPING_KB_ID`, `WARRANTY_KB_ID`, `AGENT_LOG_GROUP`,
  `GUARDRAIL_ID` and `GUARDRAIL_VERSION`
  (`test_deploy.py::test_runtime_created_with_public_http_and_every_env_var`).
- **Memory uses `summaryMemoryStrategy` with `eventExpiryDuration=7`.**
  (`test_memory_observability.py::test_memory_uses_session_summary_with_seven_day_expiry`).

## Not proven offline

**Whether the model follows the routing prompt.** This is the big one.
Tool selection in the harness is `harness/scripted_model.py`: a rule-based
planner, not Claude. A green run here means *the wiring is correct*, never
*the model behaves*. Specifically unanswered:

- Does the Orchestrator actually call `route_to_policy_agent` for a policy
  question, or answer it directly from its own weights?
- Does it call a tool at all before responding, on every turn, the way the
  routing rules demand?
- Does the Policy coordinator call `search_all_policies` before answering,
  every time, as its system prompt insists — or does it sometimes recite
  policy from memory instead?

**Whether the guardrail actually blocks anything.** The adversarial suite's
offline mode (`scripts/run_adversarial.py --offline`,
`evidence/offline/adversarial/`) checks only that the guardrail
**configuration** — the request payload `create_guardrail()` builds — covers
each of six hostile-prompt categories. Nothing in the offline stub enforces
a guardrail; `_StubClient` records the call and returns a fabricated
response. Configuration coverage and enforcement are different claims, kept
separate throughout that evidence directory's own `INDEX.md`.

**Whether retrieval returns relevant passages.** `harness/kb_fixtures.py`
ranks by literal term overlap between the query and canned passages. Real
retrieval uses Titan embeddings (`amazon.titan-embed-text-v2:0`) against a
real S3 Vectors index. A semantically obvious query with little shared
vocabulary may retrieve correctly against Titan and miss here, or the
reverse.

**Whether AWS accepts any of these API calls.** `create_guardrail`,
`create_agent_runtime`, `create_memory`, the Knowledge Base and S3 Vectors
provisioning calls in `cloudshell/_deploy-e2e.template.sh` — none of them
have been made against a real account from this machine. IAM permissions,
API shape drift, service quotas and region availability are all unverified.

### The precedent: project 2's live run

This is not a hypothetical concern. [Project 2's live
run](../../agentic-ai-aws-nanodegree-project-2/evidence/run-02/INDEX.md)
against a real deployed agent scored **five of seven scenarios**, and
**both failures were the model skipping a tool call and answering from its
own weights** — invented an order status instead of calling `get_order`;
approved a refund for a fabricated amount because it called
`initiate_refund` without first looking the order up. Project 2's offline
harness had asserted the correct call ordering since its first commit and
still could not surface either failure, because its scripted planner always
calls the tool. A green offline run and a model that reliably uses its
tools are different claims, and only one of them is available here.

## How the open questions get answered

`cloudshell/deploy-e2e-v02.sh` deploys the full stack from AWS CloudShell
in one paste (`cloudshell/README.md`), runs `tests/test_agent.py all`
against it, and runs both `scripts/run_adversarial.py --live` and
`scripts/run_scenarios.py --live` against the deployed runtime. That live
run is what closes every gap in this document — it has not happened yet
(no AWS credentials on this machine, Cloud Lab not launched; see
`MEMORY.md`), and `evidence/live/` does not exist until it does.

The offline suite is not a substitute for that run. It is what makes that
run worth doing: by the time it happens, every wiring bug is already fixed,
and a failure genuinely means something about the model, the guardrail, or
AWS itself.

## Known limitations that shape what "traced" means

- **`src/demo.py` produces no X-Ray trace.** It is a do-not-modify starter
  file that imports the builder functions and calls
  `orchestrator(prompt)` directly, with no `tracer.trace_request(...)`
  wrapper anywhere. Tracing is wired into `test` mode, `chat` mode, and the
  deployed runtime via `serve` (`src/agent_orchestrator.py`'s `_serve_http`)
  — all three open a root segment. `demo` does not, and cannot without
  editing a starter file, which is out of scope. This project's own README
  and RUNBOOK say `demo` explicitly instead of repeating the brief's claim
  that tracing "works the same from test, chat, demo".
- **Prompt injection is mapped to the `MISCONDUCT` content filter as an
  inference, not a documented fact.** Bedrock Guardrails have no dedicated
  prompt-injection policy type. `evidence/offline/adversarial/INDEX.md`
  carries the same caveat inline next to that row.
- **The `_xray_*` helpers in `agent_orchestrator.py` are dead by design, not
  by oversight.** `_xray_start_trace`, `_xray_subsegment`,
  `_xray_kb_subsegments` and `_xray_end_trace` are pre-written starter
  scaffolding carried over verbatim (see `STARTER_PROVENANCE.md`). They
  duplicate what `agent_observability.AgentTracer` (via the `tool`
  decorator, `tracer.trace_request`, and `trace_kb_retrieval`) already
  does. Wiring both up would publish two independent, disagreeing trace
  trees per request, so they are left unused.

## Corrections to the Udacity brief

Verified directly against the starter files in this repository, because a
reader who trusts the brief on these three points will be wrong:

1. **`tests/test_agent.py` contains no `test_5_parallel_retrieval`.** The
   brief implies the grader checks parallel RAG; it does not — search the
   file yourself. Parallel retrieval (`ThreadPoolExecutor(max_workers=3)`,
   `as_completed()`) is graded only by the rubric's own reading of the code
   and the screenshot it asks for. This project's own harness carries that
   proof instead, in `test_policy_agent.py::test_retrievals_actually_overlap_in_time`.
2. **`infrastructure/starter_stack.yaml` does not create an S3 Vectors
   bucket or any vector indexes.** Its `Resources:` block is three
   `AWS::DynamoDB::Table`, two plain `AWS::S3::Bucket`, one
   `AWS::IAM::Role` and one `AWS::Logs::LogGroup` — nothing else.
   `VectorStoreBucket` is an ordinary S3 bucket that merely has "vectors"
   in its name. `cloudshell/_deploy-e2e.template.sh` provisions the real
   S3 Vectors bucket and the three `{returns,shipping,warranty}-policy-index`
   indexes itself, via the `s3vectors` CLI, before creating the Knowledge
   Bases — a step the sibling project (project 2) never needed.
3. **`config.py` ships in two variants with different model IDs.** One
   uses Claude 4.5 model IDs (`us.anthropic.claude-{haiku,sonnet}-4-5-*`,
   the copy in this repo); the other uses OpenAI `gpt-oss` IDs. This
   project's own code references only `config.ORCHESTRATOR_MODEL_ID` and
   `config.WORKER_MODEL_ID`, never a literal model string, so either
   grader variant passes without modification.

## Screenshot and transcript honesty

Nothing under `evidence/offline/` fabricates a trace id, a guardrail
verdict, or a passing scenario. Where the offline stand-in genuinely cannot
answer a question — an X-Ray trace, a real enforcement decision — the
transcript says so in words rather than inventing a plausible-looking
value. `evidence/README.md` states the same rule for the eventual
`evidence/live/` directory.
