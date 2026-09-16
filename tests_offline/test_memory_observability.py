"""Tests for Task 10: Memory and Observability configuration."""
from harness import fakes
from harness.model_validation import validate_request


def test_memory_uses_session_summary_with_seven_day_expiry(orchestrator):
    fakes.recorded.clear()
    arn = orchestrator.configure_memory("arn:aws:bedrock-agentcore:us-east-1:0:runtime/x")

    req = fakes.recorded["create_memory"][-1]
    validate_request("bedrock-agentcore-control", "CreateMemory", req)
    assert req["eventExpiryDuration"] == 7
    assert req.get("name") and req.get("description")

    strategies = req["memoryStrategies"]
    assert any("summaryMemoryStrategy" in s for s in strategies), \
        "SESSION_SUMMARY strategy missing"
    assert arn.startswith("arn:aws:bedrock-agentcore:")


def test_observability_config_shape(orchestrator, monkeypatch):
    captured = {}

    def fake_apply(runtime_arn, logging_configuration):
        captured["arn"] = runtime_arn
        captured["cfg"] = logging_configuration

    monkeypatch.setattr(orchestrator, "apply_observability_config", fake_apply)
    orchestrator.configure_observability("arn:aws:bedrock-agentcore:us-east-1:0:runtime/x")

    import config
    cw = captured["cfg"]["cloudWatchConfig"]
    assert cw["logGroupName"] == config.AGENT_LOG_GROUP
    assert cw["logLevel"] == "INFO"
    assert cw["enabled"] is True

    xray = captured["cfg"]["xRayConfig"]
    assert xray["enabled"] is True
    assert xray["samplingRate"] == 1.0


def test_update_agent_runtime_request_matches_service_model(orchestrator, monkeypatch):
    """apply_observability_config()'s real update_agent_runtime call, model-validated.

    test_observability_config_shape above monkeypatches
    apply_observability_config() itself, so it never exercises the actual
    update_agent_runtime() request this project builds - the one BUG1's
    ParamValidationError class of mistake could just as easily hide in.
    Only enable_transaction_search() is stubbed here (it drives CloudWatch
    Transaction Search / X-Ray account settings that have nothing to do with
    the request shape under test, and would otherwise poll for an
    "aws/spans" log group moto/the offline stubs never create).
    """
    import agent_observability

    monkeypatch.setattr(agent_observability, "enable_transaction_search",
                         lambda sampling_rate: {"stubbed": True})

    fakes.recorded.clear()
    logging_configuration = {
        "cloudWatchConfig": {"logGroupName": "/offline/agent-logs",
                             "logLevel": "INFO", "enabled": True},
        "xRayConfig": {"enabled": True, "samplingRate": 1.0},
    }
    agent_observability.apply_observability_config(
        "arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/offline",
        logging_configuration,
    )

    req = fakes.recorded["update_agent_runtime"][-1]
    validate_request("bedrock-agentcore-control", "UpdateAgentRuntime", req)
    assert req["environmentVariables"]["AGENT_LOG_GROUP"] == "/offline/agent-logs"
    assert req["environmentVariables"]["AGENT_TRACING_ENABLED"] == "true"
