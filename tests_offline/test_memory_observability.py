"""Tests for Task 10: Memory and Observability configuration."""
from harness import fakes


def test_memory_uses_session_summary_with_seven_day_expiry(orchestrator):
    fakes.recorded.clear()
    arn = orchestrator.configure_memory("arn:aws:bedrock-agentcore:us-east-1:0:runtime/x")

    req = fakes.recorded["create_memory"][-1]
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
