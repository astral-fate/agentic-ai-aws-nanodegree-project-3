import pytest
from harness import scripted_model


@pytest.fixture
def orch(orchestrator):
    return orchestrator.build_orchestrator_agent(
        orchestrator.build_inventory_agent(),
        orchestrator.build_refund_agent(),
        orchestrator.build_policy_agent(),
        orchestrator.build_communication_agent(),
    )


def test_has_exactly_five_routing_tools(orch):
    assert set(orch.tool_registry.registry) == {
        "initialize_session",
        "route_to_inventory_agent", "route_to_policy_agent",
        "route_to_refund_agent", "route_to_communication_agent"}


def test_orchestrator_model_and_temperature(orch):
    import config
    assert orch.model.config["model_id"] == config.ORCHESTRATOR_MODEL_ID
    assert orch.model.config["temperature"] == 0.0


def _route(orch, prompt):
    scripted_model.reset_calls()
    orch(prompt)
    return [t for a, t in scripted_model.calls if a == "OrchestratorAgent"]


def test_return_request_routes_inventory_then_refund(orch):
    seq = _route(orch, "I want to return my order ORD-27176 (CUST-001)")
    assert seq[0] == "initialize_session"
    assert seq.index("route_to_inventory_agent") < seq.index("route_to_refund_agent")
    assert seq[-1] == "route_to_communication_agent"


def test_policy_question_routes_to_policy(orch):
    # NOTE: intentionally avoids the words "return"/"refund"/"order status"/
    # "track" - the scripted harness (harness/scripted_model.py, pre-written
    # and not to be modified) classifies purely on those substrings in the
    # prompt text, with no reference to the orchestrator's system prompt or
    # model at all. "return policy" would trip its is_return branch before
    # ever reaching the policy fallback, which would make this test exercise
    # the wrong rule regardless of how build_orchestrator_agent is written.
    seq = _route(orch, "What is the shipping policy for premium customers?")
    assert "route_to_policy_agent" in seq
    assert "route_to_refund_agent" not in seq
    assert seq[-1] == "route_to_communication_agent"


def test_account_question_goes_to_inventory_never_policy(orch):
    """Rule 4 - the policy agent knows policy text, not customer data."""
    seq = _route(orch, "Am I premium? (CUST-001)")
    assert "route_to_inventory_agent" in seq
    assert "route_to_policy_agent" not in seq


def test_math_question_routes_to_no_worker(orch):
    seq = _route(orch, "How much are 5 items at $29.99 with 10% off?")
    assert not any(t.startswith("route_to_") and t != "route_to_communication_agent"
                   for t in seq)
    assert seq[-1] == "route_to_communication_agent"


@pytest.mark.parametrize("prompt", [
    "I want to return my order ORD-27176 (CUST-001)",
    "What is the shipping policy for premium customers?",
    "Am I premium? (CUST-001)",
    "How much are 5 items at $29.99 with 10% off?",
])
def test_communication_is_always_the_last_call(orch, prompt):
    assert _route(orch, prompt)[-1] == "route_to_communication_agent"


def test_routing_tools_thread_the_version_they_just_read(orchestrator, orch):
    """Each routing tool must pass the version it just read, not a constant."""
    scripted_model.reset_calls()
    orch("I want to return my order ORD-27176 (CUST-001)")
    state = orchestrator._read_workflow_state("s-offline")
    assert int(state["version"]) >= 3, \
        "WorkflowState did not advance once per routing tool"
    assert "inventory_agent" in state and "communication_agent" in state
