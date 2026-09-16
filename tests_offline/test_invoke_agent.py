"""Guard `invoke_agent()`'s request shape against botocore's real API model.

The starter shipped this function calling
`invoke_agent_runtime(sessionId=..., inputText=...)`. Offline it looked fine —
nothing exercised it, and no test asserted its shape. Live it failed on every
scenario and every adversarial case before reaching a model at all:

    Missing required parameter in input: "payload"
    Unknown parameter in input: "sessionId" / "inputText"

That cost a full live run to discover. These tests validate the request
against botocore's own service model, so the same class of mistake fails here
instead of in CloudShell.
"""
import json

import pytest
from botocore.exceptions import ParamValidationError

from harness import fakes
from harness.model_validation import validate_request


def _recorded_invoke(orchestrator, session_id="s-live-aed4555d"):
    """Call invoke_agent() against the stub and return the recorded kwargs."""
    fakes.recorded.pop("invoke_agent_runtime", None)
    orchestrator.invoke_agent(session_id, "CUST-001", "where is my order?")
    calls = fakes.recorded.get("invoke_agent_runtime")
    assert calls, "invoke_agent() never called invoke_agent_runtime"
    return calls[-1]


def test_invoke_request_matches_the_real_api_model(orchestrator):
    """The exact kwargs we send must satisfy bedrock-agentcore's input shape."""
    validate_request("bedrock-agentcore", "InvokeAgentRuntime",
                     _recorded_invoke(orchestrator))


def test_the_original_starter_shape_is_genuinely_rejected():
    """The shape the starter shipped must fail — otherwise this guard is inert.

    Without this, a regression back to sessionId/inputText could pass the test
    above if the validator were misconfigured.
    """
    with pytest.raises(ParamValidationError):
        validate_request("bedrock-agentcore", "InvokeAgentRuntime", {
            "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:0:runtime/x",
            "sessionId": "s-live-aed4555d",
            "inputText": "where is my order?",
        })


def test_runtime_session_id_meets_the_33_char_minimum(orchestrator):
    """AgentCore rejects a shorter runtimeSessionId; ours are 15 chars."""
    req = _recorded_invoke(orchestrator, session_id="s-live-aed4555d")
    assert len(req["runtimeSessionId"]) >= 33
    assert len(req["runtimeSessionId"]) <= 256


def test_long_session_ids_are_passed_through_not_padded(orchestrator):
    """A session id already long enough must not be mangled."""
    long_id = "s-" + ("a" * 40)
    req = _recorded_invoke(orchestrator, session_id=long_id)
    assert req["runtimeSessionId"] == long_id


def test_payload_carries_the_keys_serve_mode_reads(orchestrator):
    """The payload IS the JSON body `serve` parses — the keys must match."""
    req = _recorded_invoke(orchestrator)
    body = json.loads(req["payload"].decode("utf-8"))
    assert set(body) >= {"session_id", "customer_id", "prompt"}
    assert body["customer_id"] == "CUST-001"
    assert "where is my order?" in body["prompt"]
