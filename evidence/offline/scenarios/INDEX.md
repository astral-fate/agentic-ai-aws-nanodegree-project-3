# Scenario transcripts -- OFFLINE

Run in-process against the real five-agent graph and `harness/scripted_model.py` (a rule-based stand-in for the model, not a live LLM decision) - no deployed runtime, no X-Ray trace. This shows the tool wiring is correct for these three prompts, not that a real model would route them the same way.

| scenario | message | expected routing | agents actually called |
|---|---|---|---|
| 01-return-order | I want to return my order ORD-27176 | Orchestrator -> Inventory -> Refund -> Communication | OrchestratorAgent -> InventoryAgent -> RefundAgent -> CommunicationAgent |
| 02-policy-question | What is the return policy for premium customers? | Orchestrator -> Policy (3 parallel retrievers: returns, shipping, warranty) -> Communication | OrchestratorAgent -> PolicyAgent -> ReturnsPolicyRetrieverAgent -> ShippingPolicyRetrieverAgent -> WarrantyPolicyRetrieverAgent -> CommunicationAgent |
| 03-direct-math | How much are 5 items at $29.99 with 10% off? | Orchestrator answers directly - no worker routing | OrchestratorAgent -> CommunicationAgent |

Per-scenario transcripts: `scenarios/<scenario-id>.txt`
