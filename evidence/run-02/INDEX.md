# run-02 — live AWS

Account `212626318772`, `us-east-1`. Produced by `cloudshell/deploy-e2e-v09.sh`
plus a local `python src/agent_orchestrator.py test` run in CloudShell.

**Both required submission screenshots are here.** This directory is the live
counterpart to [`../run-01`](../run-01/INDEX.md), which is the offline run.

## Screenshots

| File | What it shows | Required? |
|---|---|---|
| [`01-test-score-120-of-120.png`](screenshots/01-test-score-120-of-120.png) | Every task's PASS lines and `Score: 120/120 pts (100%)` in one frame | **Yes** |
| [`01c-test-score-full-task-breakdown.png`](screenshots/01c-test-score-full-task-breakdown.png) | The same run, Tasks 2–6 broken out per check | supporting |
| [`01b-test-score-tail.png`](screenshots/01b-test-score-tail.png) | Tail of the same run, through packaging | supporting |
| [`02-xray-service-map.png`](screenshots/02-xray-service-map.png) | X-Ray Trace Map: `Client → NovaMart…estrator` → PolicyAgent, CommunicationAgent, KnowledgeBase:returns, KnowledgeBase:warranty — all `Remote` nodes | **Yes** |
| [`03-scenario-run-with-trace-ids.png`](screenshots/03-scenario-run-with-trace-ids.png) | The three brief scenarios running live, each printing its X-Ray trace id | supporting |

## Graded result

**120/120 (100%).** Tasks 2 (40), 3 (20), 4 (15), 5 (25), 6 (20).

Deployed resources:

```
runtime    arn:aws:bedrock-agentcore:us-east-1:…:runtime/udacity_agentcore_runtime-LDwQ0gH9X2
guardrail  vsx504bp4kea (version 1)
KBs        returns 61JQLUIHYY · shipping AL8HEH5D7V · warranty HRQX4Y3ENP
```

## What the live run proved that the offline suite could not

The three scenarios executed against real Bedrock models, and the routing held:

- **Rule 2** — the return request went `initialize_session → inventory → refund → communication`, in that order.
- **Rule 5** — the math question routed to no worker and answered directly: `$134.96` for 5 × $29.99 less 10%, which is correct.
- **Rule 6** — the communication agent was the last call on every request.
- **Parallel multi-agent RAG is real.** The trace shows
  `[PARALLEL RETRIEVAL - START] Spawning 3 sub-agents concurrently via ThreadPoolExecutor`,
  all three Knowledge Bases responding in **4.2s**, returning genuine passages —
  the 60-day premium return window came back verbatim from the synced returns KB.
- **The retrievers appear as their own Service Map nodes**, which is why they are
  invoked as sub-agents rather than as plain in-tool function calls.

## What it also showed, honestly

The wiring is correct; the model under-performed in two places, both visible in
`03-scenario-run-with-trace-ids.png`:

- The InventoryAgent asked the customer for a customer id that was already in
  the prompt.
- The CommunicationAgent said it had no policy details moments after the
  PolicyAgent had retrieved them and written them to WorkflowState.

These runs used `openai.gpt-oss-20b-1:0` / `openai.gpt-oss-120b-1:0` via the
`ORCHESTRATOR_MODEL_ID` / `WORKER_MODEL_ID` overrides, because invoking the
Claude models on this account returned:

```
ResourceNotFoundException: Model use case details have not been submitted for
this account. Fill out the Anthropic use case details form before using the model.
```

That is an account-enablement gate, not a code defect, and the grader accepts
either model family. A larger model would likely handle the two cases above
better — but that is a claim about model quality, and nothing here demonstrates
it.

This is exactly the distinction [`../../docs/TESTING.md`](../../docs/TESTING.md)
draws: the offline harness proves the wiring, and only a live run shows whether
the model follows the prompt it is given.

## Not verified live

- `--teardown`. The cleanup ownership fix (recognising the `novamart-*-policy-kb`
  names) landed after these runs and has not been exercised against AWS.
- The deployed AgentCore Runtime serving traffic. It reaches `READY`, but each
  invocation fails with `RuntimeClientError: Runtime initialization time
  exceeded. Please make sure that initialization completes in 30s.` — importing
  `strands`, six boto3 clients and `config.py`'s two AWS round-trips does not fit
  that budget. The Service Map above comes from the local `test` path, which is
  what the project brief specifies for producing it.
