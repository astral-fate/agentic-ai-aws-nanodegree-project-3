import datetime as dt
import pytest


def _days_ago(n):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=n)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def refund(orchestrator):
    return orchestrator.build_refund_agent()


def test_has_exactly_two_tools(refund):
    assert set(refund.tool_registry.registry) == {
        "get_inventory_context", "initiate_refund"}


def test_uses_worker_model_at_temperature_point_one(refund):
    import config
    assert refund.model.config["model_id"] == config.WORKER_MODEL_ID
    assert refund.model.config["temperature"] == 0.1


@pytest.mark.parametrize("tier,days,eligible", [
    ("Standard", 29, True),
    ("Standard", 31, False),
    ("Premium",  59, True),
    ("Premium",  61, False),
])
def test_return_window_boundaries(orchestrator, refund, tier, days, eligible):
    """Standard = 30 days, Premium = 60. Test either side of each edge."""
    import boto3, config
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    sid = f"s-{tier}-{days}"
    ddb.Table(config.ORDERS_TABLE).put_item(
        Item={"customer_id": "CUST-900", "order_id": "ORD-900",
              "status": "DELIVERED", "amount": "50.00",
              "delivered_at": _days_ago(days)})
    orchestrator._create_workflow_state(sid, "CUST-900")
    state = orchestrator._read_workflow_state(sid)
    orchestrator._update_workflow_state(sid, {
        "inventory_agent": {"tier": tier, "order_id": "ORD-900",
                            "customer_id": "CUST-900",
                            "delivered_at": _days_ago(days),
                            "status": "DELIVERED"}},
        expected_version=int(state["version"]))

    result = refund.tool_registry.registry["initiate_refund"](
        session_id=sid, customer_id="CUST-900", order_id="ORD-900")
    assert result["eligible"] is eligible
    assert result["window_days"] == (60 if tier == "Premium" else 30)


def test_get_inventory_context_reads_workflow_state(orchestrator, refund):
    sid = "s-ctx"
    orchestrator._create_workflow_state(sid, "CUST-901")
    state = orchestrator._read_workflow_state(sid)
    orchestrator._update_workflow_state(
        sid, {"inventory_agent": {"tier": "Premium"}},
        expected_version=int(state["version"]))
    ctx = refund.tool_registry.registry["get_inventory_context"](session_id=sid)
    assert ctx["tier"] == "Premium"
