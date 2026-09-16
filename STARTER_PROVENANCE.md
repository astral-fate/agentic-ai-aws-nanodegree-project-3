# Starter File Provenance

This project builds on Udacity-authored starter code for the "Agentic AI"
nanodegree's AWS AgentCore multi-agent customer support project. The
original starter repository is not public; it was recovered from two
public student solution repositories that both forked/vendored the same
Udacity starter tree and left the scaffolding largely intact around their
own (differing) implementations:

- **A** = `NovaMart` (a completed student submission)
- **B** = `udacity-aws-multi-agent-support-system` (a completed student submission)

For every file below we diffed A and B. Where the bytes are byte-for-byte
identical between the two independent repos, that is strong evidence the
file is untouched Udacity scaffolding (two unrelated students would not
coincidentally reproduce the exact same non-graded file). Where the files
differ, the difference is in the *graded* portion the students wrote
(model IDs, docstring wording), and we picked the copy that best matches
this project's spec (see notes column).

**`src/agent_orchestrator.py` is the one graded file we author ourselves.**
Both reference repos contain a *completed student solution* for it, and we
must not ship someone else's answers as our own graded deliverable. It is
therefore documented separately below, in its own section, and is
intentionally **not** included in the hash table that
`tests_offline/test_provenance.py` checks — that test asserts it must
never appear there (`test_orchestrator_is_not_listed_as_starter`).

## Untouched starter files

Placed byte-for-byte as found, hashed with SHA-256.

| File | SHA-256 (prefix) | Corroboration |
|---|---|---|
| `config.py` | `b706429e382f113f…` | Taken from **B** — the only copy with the Claude 4.5 model IDs this spec requires. A's copy uses OpenAI `gpt-oss` model IDs instead — the Udacity workspace ships two starter variants, and A's `tests/test_agent.py` accepts either model family while B's accepts Claude only. B matches the model table in the project brief, so B's `config.py` was taken and A's was rejected for that reason. |
| `tests/test_agent.py` | `0baa861676cc3a56…` | Taken from **A** — same length (540 lines) as B's copy in both repos, differing only in `test_2_7_routing_uses_different_models`: A's version accepts either the Claude model family *or* the `gpt-oss` family, which is the more permissive/portable check and does not hard-fail on the different model IDs used in A's `config.py`. B's version hard-requires Claude naming. Also differs from B in one comment (`-` vs `—`), which is cosmetic. |
| `infrastructure/seed_data.py` | `2e35fa075b1ce92b…` | Byte-for-byte identical in **A and B**. |
| `infrastructure/starter_stack.yaml` | `2ad126d1c603e72b…` | Byte-for-byte identical in **A and B**. |
| `src/agent_utils.py` | `7ab84a3293c04131…` | Byte-for-byte identical in **A and B**. |
| `src/agent_observability.py` | `fb2f1a57c9498b11…` | See "§3.2 decision" below — taken from **A** only (723 lines); B's 148-line copy does not define a symbol the orchestrator imports. |
| `src/bedrock_kb_retrieval.py` | `9e1852c1a4d3754d…` | Byte-for-byte identical in **A and B**. |
| `src/demo.py` | `979f99c2190e1666…` | Byte-for-byte identical in **A and B**. |
| `.env.example` | `acfa667e67198148…` | Taken from **A** (not required by the task-1 brief's hash table, included here anyway since it was placed from a reference repo). Contains no secrets — every value is either a placeholder or blank. |

## `src/agent_observability.py` — §3.2 decision

The brief's default is B's 148-line version. Before accepting that default,
we checked whether the untouched files (and A's reference implementation of
`agent_orchestrator.py`, used only to see what the pre-written import
expects) reference a name the 148-line version does not define:

```
$ grep -n "agent_observability" src/demo.py tests/test_agent.py <A>/src/agent_orchestrator.py
<A>/src/agent_orchestrator.py:55:from agent_observability import apply_observability_config
```

`src/demo.py` and `tests/test_agent.py` do not reference
`agent_observability` at all, but `agent_orchestrator.py` (which every
later task builds out from the skeleton we create in this task) imports
`apply_observability_config` from it. We then parsed both candidate files
with `ast` to list their top-level definitions:

- B's 148-line version defines: `ROOT_SERVICE_NAME`, `_REMOTE`,
  `_Request`, `_close`, `_current`, `_document`, `_lock`, `_new_span`,
  `_open`, `_xray`, `agent_end`, `agent_start`, `end_request`, `kb_end`,
  `kb_start`, `request_trace`, `start_request` — **no**
  `apply_observability_config`.
- A's 723-line version defines (among others) `apply_observability_config`,
  `AgentTracer`, `CloudWatchLogHandler`, `enable_transaction_search`,
  `flush_logs`, `setup_logging`, `tool`, `tracer`, `validate_logging_configuration`,
  `wait_for_runtime_ready` — **it does** define the required symbol.

Per the brief's decision rule ("If the 148-line version defines
`apply_observability_config`, keep it. If it does not, replace it with
A's 723-line version and note why"), we used **A's 723-line version**.
Importing B's 148-line version would break every downstream task the
moment `agent_orchestrator.py`'s `configure_observability()` calls
`apply_observability_config()`, since the name simply would not exist.

## `agent_orchestrator.py` — derived from the starter, then authored by us

This file is **not** a starter file and is intentionally excluded from the
hash table above (and from `tests_offline/test_provenance.py`'s checked
set — see `test_orchestrator_is_not_listed_as_starter`). Its SHA-256 will
change as later tasks implement it; recording a hash for it here would be
meaningless and would break the "starter files must not drift" invariant
the provenance test enforces.

Its origin: both reference repos (A and B) ship a **complete student
solution** for `src/agent_orchestrator.py`. Shipping either as our graded
deliverable would mean submitting someone else's implementation as our
own. Instead, we started from A's 1793-line file (chosen because A's
config/tests were themselves cross-checked as the more portable variants
above) and:

1. Kept every pre-written region verbatim: the imports and module-level
   boto3 clients; `_register_agentcore_compat_methods`;
   `_register_agentcore_control_compat_methods`; `_create_workflow_state`,
   `_read_workflow_state`, `_update_workflow_state`; the `trace` singleton;
   all `_xray_*` functions; the S3-packaging half of
   `deploy_to_agentcore_runtime`; all `_gw_*` functions;
   `deploy_agentcore_gateway`; `invoke_agent`; `deploy_all`; and the
   `__main__` dispatch block. (A does not define a separate
   `_apply_guardrail` function, so there was nothing to preserve there.)
2. Emptied every one of the 34 TODO comment sites back to just their
   original `# TODO:` comment block, deleting all executable code that
   followed each one (including any nested helper/tool function
   definitions the reference solution had filled in), while keeping each
   enclosing function's `def` line, signature, and docstring intact. Where
   a builder function's entire body was TODO-covered (all of
   `build_inventory_agent`, `build_refund_agent`, `build_communication_agent`,
   `build_orchestrator_agent`), the result is exactly the docstring plus the
   TODO comments and nothing else executable. Where a function had
   pre-written scaffolding that was not itself part of any TODO (the
   `retriever_model` shared by the three policy retrievers in
   `build_policy_agent`; the "does this already exist?" reuse checks in
   `create_guardrail`, `deploy_to_agentcore_runtime`, and
   `configure_memory`; the S3 packaging in `deploy_to_agentcore_runtime`),
   that scaffolding was kept and only the TODO-covered code after it was
   removed.
3. Verified the result still parses: `python -c "import ast;ast.parse(open('src/agent_orchestrator.py',encoding='utf-8').read())"` succeeds, and `python -m py_compile` succeeds.

Current state: 1144 lines, 34 empty `# TODO:` stubs, everything else
byte-identical to A's pre-written regions (diffed and confirmed).
