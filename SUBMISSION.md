# Submission — rubric mapping

Every criterion the grader (`tests/test_agent.py`) and the written rubric
check, mapped to the file, line, test, or screenshot that satisfies it.
Line numbers are into [`src/agent_orchestrator.py`](src/agent_orchestrator.py)
unless stated otherwise.

**Read this first:** the offline evidence in `evidence/run-01/` and
`evidence/offline/` comes from a harness with no AWS account, `moto`
DynamoDB, and a scripted, rule-based model stand-in — not a live LLM. What
that does and does not establish is set out precisely in
[`docs/TESTING.md`](docs/TESTING.md), and summarised at the bottom of this
page. The two required screenshots are **pending the live run** — see
`MEMORY.md` and `docs/RUNBOOK.md` for why it has not happened yet.

`tests/test_agent.py all` totals **120/120 points** when run against a real
deployed stack (40 + 20 + 15 + 25 + 20, across Tasks 2-6 below); that total
has not been produced from this machine.

---

## Task 2 — Multi-Agent Orchestration (40 pts)

| Requirement | Where | Evidence |
|---|---|---|
| `build_inventory_agent()` returns an `Agent`, 3 tools (5+5 pts) | [`:412-504`](src/agent_orchestrator.py#L412) | `tests/test_agent.py::test_2_1`, `::test_2_2`; offline: `tests_offline/test_inventory_agent.py::test_has_exactly_three_tools` |
| `build_policy_agent()` returns an `Agent`, 1 tool (5+5 pts) | [`:631-804`](src/agent_orchestrator.py#L631) | `test_agent.py::test_2_3`, `::test_2_4`; offline: `tests_offline/test_policy_agent.py::test_coordinator_has_exactly_one_tool` |
| `build_orchestrator_agent()` returns an `Agent`, 5 routing tools (5+5 pts) | [`:870-1040`](src/agent_orchestrator.py#L870) | `test_agent.py::test_2_5`, `::test_2_6`; offline: `tests_offline/test_orchestrator.py::test_has_exactly_five_routing_tools` |
| Orchestrator uses `config.ORCHESTRATOR_MODEL_ID`; workers use `config.WORKER_MODEL_ID` (5+5 pts) | [`:420-423`](src/agent_orchestrator.py#L420), [`:880-883`](src/agent_orchestrator.py#L880) | `test_agent.py::test_2_7`; offline: `test_orchestrator.py::test_orchestrator_model_and_temperature`, `test_inventory_agent.py::test_uses_worker_model_and_temperature` |

Refund (2 tools) and Communication (1 tool) tool counts are graded by the
written rubric, not `test_agent.py`, per `docs/superpowers/plans/…`'s
Global Constraints. Offline: `tests_offline/test_refund_agent.py::test_has_exactly_two_tools`,
`tests_offline/test_communication_agent.py`.

## Task 3 — AgentCore Deployment + Guardrails (20 pts)

| Requirement | Where | Evidence |
|---|---|---|
| Guardrail named `config.GUARDRAIL_NAME` exists (10 pts) | [`:1047-1139`](src/agent_orchestrator.py#L1047) | `test_agent.py::test_3_1`; offline: `tests_offline/test_guardrail.py::test_guardrail_request_has_every_required_policy` |
| Guardrail has content, PII and topic policies (5 pts) | [`:1072-1119`](src/agent_orchestrator.py#L1072) | `test_agent.py::test_3_2`; same offline test as above; policy detail in [`docs/SECURITY.md`](docs/SECURITY.md) |
| `AGENTCORE_RUNTIME_ARN` set — runtime deployed (5 pts) | [`:1142-1247`](src/agent_orchestrator.py#L1142) | `test_agent.py::test_3_3`; offline: `tests_offline/test_deploy.py::test_runtime_created_with_public_http_and_every_env_var` |
| Guardrail version numbered, not `DRAFT` (rubric item, not separately pointed) | [`:1132-1139`](src/agent_orchestrator.py#L1132) | offline: `tests_offline/test_guardrail.py::test_guardrail_is_versioned_not_draft` |
| Runtime uses `PUBLIC` network mode, `HTTP` protocol, all 8 env vars (rubric item) | [`:1232-1243`](src/agent_orchestrator.py#L1232) | offline: `tests_offline/test_deploy.py::test_runtime_created_with_public_http_and_every_env_var` |

## Task 4 — Memory (15 pts)

| Requirement | Where | Evidence |
|---|---|---|
| AgentCore Memory enabled, `SESSION_SUMMARY` type (15 pts) | [`:1254-1306`](src/agent_orchestrator.py#L1254) | `test_agent.py::test_4_1`; offline: `tests_offline/test_memory_observability.py::test_memory_uses_session_summary_with_seven_day_expiry` |
| `summaryMemoryStrategy` with `eventExpiryDuration=7` (rubric item) | [`:1283-1291`](src/agent_orchestrator.py#L1283) | same offline test |

## Task 5 — Bedrock Knowledge Bases (25 pts)

| Requirement | Where | Evidence |
|---|---|---|
| `RETURNS_KB_ID` set and `ACTIVE` (8 pts) | `config.py:108`, provisioned by `cloudshell/_deploy-e2e.template.sh` phase 6 | `test_agent.py::test_5_1` — **live only, not yet run** |
| `SHIPPING_KB_ID` set and `ACTIVE` (8 pts) | `config.py:109`, same phase | `test_agent.py::test_5_2` — **live only, not yet run** |
| `WARRANTY_KB_ID` set and `ACTIVE` (9 pts) | `config.py:110`, same phase | `test_agent.py::test_5_3` — **live only, not yet run** |
| Three parallel retriever sub-agents, one KB each (rubric item) | [`:646-721`](src/agent_orchestrator.py#L646) | offline: `tests_offline/test_policy_agent.py::test_returns_results_from_all_three_knowledge_bases`; architecture rationale in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| `search_all_policies` invokes all three retrievers **in parallel** via `ThreadPoolExecutor(max_workers=3)` + `as_completed()` (rubric item — **not** checked by `test_agent.py`, see `docs/TESTING.md`'s corrections) | [`:727-777`](src/agent_orchestrator.py#L727) | offline: `tests_offline/test_policy_agent.py::test_retrievals_actually_overlap_in_time`, `::test_search_all_policies_actually_invokes_each_retriever_agent` |
| One KB failing does not lose the other two (design goal, not separately rubric-worded) | [`:763-771`](src/agent_orchestrator.py#L763) | offline: `tests_offline/test_policy_agent.py::test_one_failing_retriever_does_not_lose_the_others` |

`config.py`'s Knowledge Base fields are populated only by a real deploy;
Task 5's three grader checks cannot pass offline by construction (a KB
either exists in a real account and is `ACTIVE`, or it does not exist).

## Task 6 — Observability (20 pts)

| Requirement | Where | Evidence |
|---|---|---|
| CloudWatch logging enabled on the runtime (10 pts) | [`:1314-1347`](src/agent_orchestrator.py#L1314) | `test_agent.py::test_6_1`; offline: `tests_offline/test_memory_observability.py::test_observability_config_shape` |
| X-Ray tracing enabled, 100% sampling (10 pts) | same | `test_agent.py::test_6_2`; same offline test |
| X-Ray Service Map shows `NovaMart-Orchestrator` connected to worker nodes, including the PolicyAgent and KnowledgeBase agents (screenshot requirement, not a `test_agent.py` point) | `tool`/`tracer`/`trace_kb_retrieval` wiring, [`:64`](src/agent_orchestrator.py#L64) and call sites documented in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | **`evidence/live/screenshots/02-xray-service-map.png` — REQUIRED, PENDING the live run** |

## Adversarial guardrail suite (stand-out extra, not separately pointed)

| Requirement | Where | Evidence |
|---|---|---|
| Six hostile-prompt categories checked against the guardrail configuration | [`scripts/run_adversarial.py`](scripts/run_adversarial.py) | `evidence/offline/adversarial/INDEX.md` — configuration coverage, explicitly not enforcement (see `docs/SECURITY.md`) |
| Same six prompts against the real deployed guardrail | same script, `--live` | **`evidence/live/adversarial/` — PENDING the live run** |

## Code quality & reflection

| Requirement | Where |
|---|---|
| Every `@tool` has a docstring (purpose, params, return) | every tool in `src/agent_orchestrator.py`; offline: `tests_offline/test_inventory_agent.py::test_every_tool_has_a_docstring` and equivalents in the other agent test files |
| No hardcoded model IDs | `grep -n 'anthropic\.' src/agent_orchestrator.py` returns nothing — every model reference is `config.ORCHESTRATOR_MODEL_ID` / `config.WORKER_MODEL_ID` |
| Written reflection | [`REFLECTION.md`](REFLECTION.md) |

### Screenshot checklist

| Screenshot | Required? | Status |
|---|---|---|
| `01-test-score.png` (terminal render of `tests/test_agent.py all`, showing `120/120`) | **REQUIRED** | **PENDING — live run not yet executed** |
| `02-xray-service-map.png` (Service Map, Orchestrator → Worker → KnowledgeBase) | **REQUIRED** | **PENDING — live run not yet executed** |
| `03-knowledge-bases.png` | supporting | pending |
| `04-agentcore-runtime.png` | supporting | pending |
| `05-guardrail.png` | supporting | pending |
| `06-cloudwatch-logs.png` | supporting | pending |

## Corrections to the Udacity brief (verified, not assumed)

Full detail in `docs/TESTING.md`; summarised here because a rubric mapping
that hides a discrepancy with the grading source is worse than one that
states it:

- `tests/test_agent.py` contains no `test_5_parallel_retrieval` — parallel
  RAG is graded by the written rubric's own reading of the code and the
  Service Map screenshot, not by the automated grader.
- `infrastructure/starter_stack.yaml` provisions three DynamoDB tables, two
  plain S3 buckets, an IAM role and a log group — no S3 Vectors bucket, no
  vector indexes. `cloudshell/_deploy-e2e.template.sh` provisions those
  itself before creating the Knowledge Bases.
- `config.py` ships in two variants (Claude 4.5 model IDs vs. OpenAI
  `gpt-oss` IDs); this project references only the config constants, so
  either grader variant passes.

## What is not yet demonstrated

Stated plainly, because a rubric row marked complete on the wrong evidence
is worse than one marked incomplete.

Every offline test proves **the code is wired correctly**. None of them
prove that a real Claude model follows the routing prompt, calls a tool
rather than answering from its own weights, or that the guardrail actually
blocks a hostile prompt. `docs/TESTING.md` states this in full, including
the concrete precedent: **project 2's live run scored five of seven
scenarios, and both failures were the model skipping a tool call** — a
failure mode a green offline run cannot surface. Tasks 5's three
Knowledge Base checks and the two required screenshots depend entirely on
a live deployment that has not happened on this machine (see `MEMORY.md`);
`docs/RUNBOOK.md` is the procedure that closes that gap once the Udacity
Cloud Lab is launched.
