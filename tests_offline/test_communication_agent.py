def test_has_exactly_one_tool(orchestrator):
    agent = orchestrator.build_communication_agent()
    assert set(agent.tool_registry.registry) == {"get_full_workflow_context"}


def test_temperature_is_point_three(orchestrator):
    import config
    agent = orchestrator.build_communication_agent()
    assert agent.model.config["model_id"] == config.WORKER_MODEL_ID
    assert agent.model.config["temperature"] == 0.3


def test_context_tool_returns_every_agent_column(orchestrator):
    sid = "s-comm"
    orchestrator._create_workflow_state(sid, "CUST-002")
    v = int(orchestrator._read_workflow_state(sid)["version"])
    v_state = orchestrator._update_workflow_state(
        sid, {"inventory_agent": {"tier": "Premium"}}, expected_version=v)
    orchestrator._update_workflow_state(
        sid, {"refund_agent": {"eligible": True}},
        expected_version=int(v_state["version"]))

    agent = orchestrator.build_communication_agent()
    ctx = agent.tool_registry.registry["get_full_workflow_context"](session_id=sid)
    assert ctx["inventory_agent"]["tier"] == "Premium"
    assert ctx["refund_agent"]["eligible"] is True
    assert ctx["customer_id"] == "CUST-002"
