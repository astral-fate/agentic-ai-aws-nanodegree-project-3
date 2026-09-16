import pytest

from harness import scripted_model, kb_fixtures


def test_kb_fixtures_are_domain_specific():
    returns = kb_fixtures.passages("KBRETURNS01")
    shipping = kb_fixtures.passages("KBSHIPPING1")
    assert returns and shipping
    assert all({"text", "source", "score"} <= set(p) for p in returns)
    assert "return" in returns[0]["text"].lower()
    assert returns != shipping


@pytest.mark.xfail(reason="build_inventory_agent lands in Task 4")
def test_scripted_model_records_tool_calls(orchestrator):
    scripted_model.reset_calls()
    agent = orchestrator.build_inventory_agent()
    agent("What is the status of order ORD-27176 for CUST-001?")
    tools = [t for _, t in scripted_model.calls]
    assert "check_order_status" in tools
