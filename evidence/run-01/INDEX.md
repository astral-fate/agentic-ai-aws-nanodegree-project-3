# run-01 — offline harness

**This is the offline run: no AWS account, `moto` DynamoDB/CloudFormation,
a scripted rule-based model stand-in — not a live LLM.** Produced by
`python -m pytest tests_offline/ -v` on this machine, tee'd to
[`pytest_output.txt`](pytest_output.txt).

**61 / 61 passed.**

## Results by file

| File | Tests | What it covers |
|---|---|---|
| `test_provenance.py` | 2 | Starter files match the hashes recorded in `STARTER_PROVENANCE.md`; `agent_orchestrator.py` is not itself listed as a starter file |
| `test_bootstrap.py` | 3 | The harness's own import ordering (moto up, fakes registered, then `agent_orchestrator` imported) |
| `test_scripted_model.py` | 2 | The rule-based model stand-in itself |
| `test_cloudshell_build.py` | 4 | The generated `deploy-e2e-v02.sh` embeds real project files with no leftover placeholders, is `bash -n` clean |
| `test_inventory_agent.py` | 5 | 3 tools, worker model/temperature, composite-key lookup, docstrings |
| `test_refund_agent.py` | 7 | 2 tools, temperature, return-window boundaries (29/31 Standard, 59/61 Premium), reads `WorkflowState` |
| `test_policy_agent.py` | 6 | 1 tool, temperature, all three KB domains present, retrievers actually invoked (not bypassed), retrievals overlap in time, one failing retriever does not lose the others |
| `test_communication_agent.py` | 3 | 1 tool, reads the full `WorkflowState` |
| `test_orchestrator.py` | 12 | 5 routing tools, model/temperature, the three brief scenarios route correctly, account questions never reach Policy, communication is always last, session ownership refusal, version threading |
| `test_workflow_state.py` | 2 | Stale-version writer retries and recovers with no lost column; exhausted retries raise |
| `test_guardrail.py` | 2 | Guardrail request carries every required policy; version is numbered, not `DRAFT` |
| `test_deploy.py` | 1 | Runtime request uses `PUBLIC`/`HTTP` and all eight environment variables |
| `test_memory_observability.py` | 2 | Memory uses `summaryMemoryStrategy` with `eventExpiryDuration=7`; observability config shape |
| `test_adversarial.py` | 2 | Guardrail-request configuration coverage for the six adversarial cases |
| `test_scenarios.py` | 3 | The harness's own recorded tool-call sequence matches each brief scenario's expected routing; no trace id is ever fabricated offline |
| `test_cleanup.py` | 5 | `cleanup.py`'s dry-run default, deletion ordering, no-credentials degradation |
| **Total** | **61** | |

## Also here

- [`pytest_output.txt`](pytest_output.txt) — the full verbose run, captured
  directly from `pytest tests_offline/ -v`, not hand-written.
- `../offline/scenarios/` — the three Udacity-brief scenario transcripts,
  produced by `scripts/run_scenarios.py --offline`.
- `../offline/adversarial/` — the six adversarial guardrail transcripts,
  produced by `scripts/run_adversarial.py --offline`.

## What this does not show

A passing offline suite proves the code is wired correctly. It does not
prove a real model follows the routing prompt, that the guardrail blocks
anything, that retrieval finds relevant passages, or that AWS accepts any
of these API calls. **[`docs/TESTING.md`](../../docs/TESTING.md)** states
the full proven/not-proven split, including the concrete precedent from
project 2's live run — five of seven scenarios passed there, and both
failures were invisible to a green offline run. Read it before citing any
row above as more than what it says.
