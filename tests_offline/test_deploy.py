from harness import fakes


def _build_orchestrator_agent(orchestrator):
    """Tasks 4-8 provide all five builders; wire them the same way
    tests_offline/test_orchestrator.py does so deploy_to_agentcore_runtime
    is exercised with a real orchestrator Agent, matching its actual
    three-positional-argument signature."""
    return orchestrator.build_orchestrator_agent(
        orchestrator.build_inventory_agent(),
        orchestrator.build_refund_agent(),
        orchestrator.build_policy_agent(),
        orchestrator.build_communication_agent(),
    )


def test_runtime_created_with_public_http_and_every_env_var(orchestrator):
    fakes.recorded.clear()
    orchestrator_agent = _build_orchestrator_agent(orchestrator)

    arn = orchestrator.deploy_to_agentcore_runtime(
        orchestrator_agent, "gr-1", "1")

    req = fakes.recorded["create_agent_runtime"][-1]
    assert req["networkConfiguration"]["networkMode"] == "PUBLIC"
    assert req["protocolConfiguration"]["serverProtocol"] == "HTTP"
    assert "agentRuntimeArtifact" in req

    env = req["environmentVariables"]
    for key in ("AWS_REGION", "PROJECT_NAME", "RETURNS_KB_ID", "SHIPPING_KB_ID",
                "WARRANTY_KB_ID", "AGENT_LOG_GROUP", "GUARDRAIL_ID",
                "GUARDRAIL_VERSION"):
        assert key in env and env[key] != "", f"{key} missing from runtime env"
    assert env["GUARDRAIL_VERSION"] != "DRAFT"
    assert arn.startswith("arn:aws:bedrock-agentcore:")
