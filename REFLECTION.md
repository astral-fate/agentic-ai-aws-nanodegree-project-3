# Reflection

**A design decision: the policy fan-out is three real sub-agents, not
three tool calls.** `search_all_policies` could have called
`retrieve_from_knowledge_base()` three times inside a thread pool with no
`Agent` objects involved — same parallelism, fewer moving parts, and it was
in fact the first implementation. I rejected it once I checked what the
rubric actually grades: the X-Ray Service Map must show
`NovaMart-Orchestrator` connected to worker nodes "including the
PolicyAgent and KnowledgeBase agents." A trace only draws a distinct node
for something invoked as a distinct unit of work. Three bare function calls
inside one tool produce one subsegment total — correct data, wrong shape.
So `_make_retriever()` builds three real `Agent` objects, one per Knowledge
Base, and `search_all_policies` invokes them as agents
(`retriever_agent(query)`), never bypassing to the retrieval function
directly. A regression test now guards specifically against reintroducing
that bypass, and was verified to fail against it before the fix landed.

**A challenge: the wrong decorator, found by checking output rather than
imports.** `agent_orchestrator.py` imported `tool` from `strands` directly
instead of `agent_observability.tool` — the version that opens an X-Ray
subsegment per call. The tool-count tests all still passed, because the
plain decorator registers tools identically; nothing failed loudly. I only
found it by running a real request through the graph in-process and
checking whether a trace was actually published — it was not. Fixing the
import alone still published nothing, because no code path ever opened the
root segment `tracer.trace_request(...)` creates; subsegments silently
no-op with no parent. The real fix needed three call sites: `test` mode,
`chat` mode, and the deployed runtime's `_serve_http`. `src/demo.py`, a
do-not-modify starter file, still traces nothing — a limitation this
project's docs state rather than hide.

**A production consideration: Knowledge Base storage bills while idle.**
Unlike DynamoDB or S3 objects, Bedrock Knowledge Base storage and its S3
Vectors index meter continuously whether or not a single query arrives.
The runbook's teardown order reflects that directly: knowledge bases and
their vector indexes are deleted first, before anything else, so an
interrupted teardown still stops the meter that actually drains a budget.

*(344 words)*
