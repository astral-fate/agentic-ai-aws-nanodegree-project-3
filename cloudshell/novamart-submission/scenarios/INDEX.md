# Scenario transcripts -- LIVE

Run against the real deployed AgentCore Runtime via `invoke_agent()`. The X-Ray trace id is a time-window lookup (see run_scenarios.py's module docstring) - if more than one candidate trace appears, all are listed rather than guessed.

| scenario | message | expected routing | X-Ray trace id(s) |
|---|---|---|---|
| 01-return-order | I want to return my order ORD-27176 | Orchestrator -> Inventory -> Refund -> Communication | (none found) |
| 02-policy-question | What is the return policy for premium customers? | Orchestrator -> Policy (3 parallel retrievers: returns, shipping, warranty) -> Communication | (none found) |
| 03-direct-math | How much are 5 items at $29.99 with 10% off? | Orchestrator answers directly - no worker routing | (none found) |

Per-scenario transcripts: `scenarios/<scenario-id>.txt`
