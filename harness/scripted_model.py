"""A rule-based stand-in for model inference.

It calls the agent's real registered tools, so tool wiring, WorkflowState
version threading and the parallel fan-out are all genuinely exercised. What it
does NOT do is decide anything the way a model would - so a green run here
says the plumbing is right, never that the agent behaves. There is no LLM
here at all: `respond()` is regex-and-keyword routing over the prompt text,
picked to exercise each tool at least once. It cannot show that a real model
would choose the same tool, follow the system prompt, or handle a prompt
this harness didn't anticipate.
"""
import re
import threading

calls: list[tuple[str, str]] = []
_lock = threading.Lock()


def reset_calls():
    with _lock:
        calls.clear()


def _record(agent_name, tool_name):
    with _lock:
        calls.append((agent_name, tool_name))


def _call(agent, name, **kwargs):
    fn = agent.tool_registry.registry[name]
    _record(agent.name or "unnamed", name)
    return fn(**kwargs)


_ORDER_RE = re.compile(r"\b(ORD-\d+)\b")
_CUST_RE = re.compile(r"\b(CUST-\d+)\b")
_SESSION_RE = re.compile(r"\b(s-[\w-]+)\b")


def respond(agent, prompt: str) -> str:
    name = (agent.name or "").lower()
    reg = agent.tool_registry.registry
    order_match = _ORDER_RE.search(prompt)
    order = order_match.group(1) if order_match else None
    cust_match = _CUST_RE.search(prompt)
    customer = cust_match.group(1) if cust_match else "CUST-001"
    session_match = _SESSION_RE.search(prompt)
    session = session_match.group(1) if session_match else "s-offline"

    if "inventory" in name:
        out = []
        if order and "check_order_status" in reg:
            out.append(str(_call(agent, "check_order_status",
                                 customer_id=customer, order_id=order)))
        if "tier" in prompt.lower() or "premium" in prompt.lower():
            out.append(str(_call(agent, "get_customer_tier", customer_id=customer)))
        if not out and "list_customer_orders" in reg:
            out.append(str(_call(agent, "list_customer_orders", customer_id=customer)))
        return " | ".join(out)

    if "refund" in name:
        ctx = _call(agent, "get_inventory_context", session_id=session)
        result = _call(agent, "initiate_refund", session_id=session,
                       customer_id=customer, order_id=order or "ORD-UNKNOWN")
        return f"context={ctx} decision={result}"

    if "policy" in name and "retriever" not in name:
        return str(_call(agent, "search_all_policies", query=prompt))

    if "retriever" in name:
        only = next(iter(reg))
        return str(_call(agent, only, query=prompt))

    if "communication" in name:
        ctx = _call(agent, "get_full_workflow_context", session_id=session)
        return f"Dear customer, {ctx}"

    if "orchestrator" in name:
        return _orchestrate(agent, prompt, customer, session)

    return ""


def _orchestrate(agent, prompt, customer, session):
    """Applies the six routing rules the Orchestrator's prompt encodes.

    This mirrors the rules so the harness can assert the tools exist and
    thread WorkflowState correctly. It does NOT prove the model would follow
    the prompt - only a live run against the real model can show that.
    """
    low = prompt.lower()
    _call(agent, "initialize_session", session_id=session, customer_id=customer)

    is_math = any(k in low for k in ("how much", "calculate", "% off", "discount of"))
    is_account = any(k in low for k in ("my tier", "am i premium", "my account"))
    is_return = any(k in low for k in ("return", "refund", "order status", "track"))

    if is_math:
        pass                                    # Rule 5 - answer directly
    elif is_account:
        _call(agent, "route_to_inventory_agent", session_id=session, query=prompt)
    elif is_return:
        _call(agent, "route_to_inventory_agent", session_id=session, query=prompt)
        _call(agent, "route_to_refund_agent", session_id=session, query=prompt)
    else:
        _call(agent, "route_to_policy_agent", session_id=session, query=prompt)

    _call(agent, "route_to_communication_agent", session_id=session, query=prompt)
    return "done"
