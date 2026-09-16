import pytest
from botocore.exceptions import ParamValidationError

from harness import fakes
from harness.model_validation import validate_request


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

    # The real, previously-missing check: does this payload actually match
    # what the bedrock-agentcore-control CreateAgentRuntime API accepts?
    # This is what catches a wrong-but-plausible shape - see
    # test_model_validator_rejects_the_old_broken_artifact_shape below for
    # proof it would have caught the exact bug that shipped.
    validate_request("bedrock-agentcore-control", "CreateAgentRuntime", req)

    env = req["environmentVariables"]
    for key in ("AWS_REGION", "PROJECT_NAME", "RETURNS_KB_ID", "SHIPPING_KB_ID",
                "WARRANTY_KB_ID", "AGENT_LOG_GROUP", "GUARDRAIL_ID",
                "GUARDRAIL_VERSION"):
        assert key in env and env[key] != "", f"{key} missing from runtime env"
    assert env["GUARDRAIL_VERSION"] != "DRAFT"
    assert arn.startswith("arn:aws:bedrock-agentcore:")


def test_model_validator_rejects_the_old_broken_artifact_shape():
    """Prove the validator would have caught the live BUG1 payload.

    The live error was:
        Invalid number of parameters set for tagged union structure
        agentRuntimeArtifact. Can only set one of the following keys:
        containerConfiguration, codeConfiguration.
        Unknown parameter in agentRuntimeArtifact: "bucket", ...

    That shape - agentRuntimeArtifact={'bucket', 'prefix', 'runtime'} at
    the top level - is exactly what the starter's TODO comment described and
    what this project shipped before the fix. Feed it to the same validator
    test_runtime_created_with_public_http_and_every_env_var now uses, and
    confirm it is rejected offline, with no AWS account required.
    """
    old_broken_request = {
        "agentRuntimeName": "udacity_agentcore_runtime",
        "roleArn": "arn:aws:iam::000000000000:role/udacity-agentcore-agentcore-role",
        "agentRuntimeArtifact": {
            "bucket":  "udacity-agentcore-policy-docs-000000000000-abc123",
            "prefix":  "agentcore-artifacts/udacity_agentcore_runtime/deployment.zip",
            "runtime": "PYTHON_3_12",
        },
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "protocolConfiguration": {"serverProtocol": "HTTP"},
        "environmentVariables": {"AWS_REGION": "us-east-1"},
    }
    with pytest.raises(ParamValidationError, match="agentRuntimeArtifact"):
        validate_request("bedrock-agentcore-control", "CreateAgentRuntime",
                          old_broken_request)


def test_an_existing_runtime_is_updated_not_silently_reused(orchestrator):
    """`deploy` must ship new code when a runtime already exists.

    The starter returned the existing ARN and stopped, which made deploy a
    no-op: the artifact was uploaded to S3 and nothing was ever pointed at it.
    Live, that hid two fixes across several runs — the container kept serving
    the first deploy's broken entry point while the script reported success.
    """
    from harness import fakes

    orch = orchestrator.build_orchestrator_agent(
        orchestrator.build_inventory_agent(), orchestrator.build_refund_agent(),
        orchestrator.build_policy_agent(), orchestrator.build_communication_agent())

    # A runtime with the expected name already exists in the account.
    import config
    runtime_name = f"{config.PROJECT_NAME}-runtime".replace("-", "_")
    fakes._RESPONSES["list_agent_runtimes"] = {"agentRuntimes": [{
        "agentRuntimeName": runtime_name,
        "agentRuntimeId":   "udacity_agentcore_runtime-EXISTING",
        "agentRuntimeArn":  "arn:aws:bedrock-agentcore:us-east-1:0:runtime/"
                            "udacity_agentcore_runtime-EXISTING",
    }]}
    fakes.recorded.pop("update_agent_runtime", None)
    fakes.recorded.pop("create_agent_runtime", None)
    try:
        orchestrator.deploy_to_agentcore_runtime(orch, "gr-1", "1")
    finally:
        fakes._RESPONSES.pop("list_agent_runtimes", None)

    updates = fakes.recorded.get("update_agent_runtime")
    assert updates, "an existing runtime was reused without being updated"
    assert not fakes.recorded.get("create_agent_runtime"), \
        "create was called even though the runtime already existed"

    req = updates[-1]
    assert req["agentRuntimeId"] == "udacity_agentcore_runtime-EXISTING"
    # The update must carry the same artifact and env as a fresh create.
    assert "codeConfiguration" in req["agentRuntimeArtifact"]
    assert req["environmentVariables"]["GUARDRAIL_ID"] == "gr-1"
    validate_request("bedrock-agentcore-control", "UpdateAgentRuntime", req)
