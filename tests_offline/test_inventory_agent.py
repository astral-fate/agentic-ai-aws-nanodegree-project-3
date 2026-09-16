import pytest


@pytest.fixture
def seeded(orchestrator):
    import boto3
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    import config
    ddb.Table(config.CUSTOMERS_TABLE).put_item(
        Item={"customer_id": "CUST-001", "name": "Ada", "tier": "Premium"})
    ddb.Table(config.ORDERS_TABLE).put_item(
        Item={"customer_id": "CUST-001", "order_id": "ORD-27176",
              "status": "DELIVERED", "amount": "139.99",
              "delivered_at": "2026-09-01T00:00:00Z"})
    return orchestrator


def test_has_exactly_three_tools(seeded):
    agent = seeded.build_inventory_agent()
    assert set(agent.tool_registry.registry) == {
        "check_order_status", "get_customer_tier", "list_customer_orders"}


def test_uses_worker_model_and_temperature(seeded):
    import config
    agent = seeded.build_inventory_agent()
    assert agent.model.config["model_id"] == config.WORKER_MODEL_ID
    assert agent.model.config["temperature"] == 0.1


def test_check_order_status_needs_both_key_parts(seeded):
    """Orders has a composite key, so a customer_id alone cannot find an order."""
    tool = seeded.build_inventory_agent().tool_registry.registry["check_order_status"]
    found = tool(customer_id="CUST-001", order_id="ORD-27176")
    assert "DELIVERED" in str(found)
    missing = tool(customer_id="CUST-001", order_id="ORD-NOPE")
    assert "ORD-NOPE" in str(missing) and "DELIVERED" not in str(missing)


def test_get_customer_tier_returns_tier(seeded):
    tool = seeded.build_inventory_agent().tool_registry.registry["get_customer_tier"]
    assert "Premium" in str(tool(customer_id="CUST-001"))


def test_every_tool_has_a_docstring(seeded):
    """Rubric item, and Strands feeds the docstring to the model as the tool description."""
    for name, fn in seeded.build_inventory_agent().tool_registry.registry.items():
        assert (fn.__doc__ or "").strip(), f"{name} has no docstring"
