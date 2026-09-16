"""
harness/model_validation.py
============================
Validate a recorded control-plane request against botocore's REAL service
model, instead of against a shape we guessed ourselves.

Why this exists: tests_offline previously asserted these control-plane
payloads (create_agent_runtime, create_guardrail, create_memory,
update_agent_runtime) against hand-written expectations that encoded the
same assumptions the production code made. That validates a payload
against itself - it cannot catch the class of bug where the assumed shape
is simply wrong, which is exactly what happened live:
`agentRuntimeArtifact` was sent as `{'bucket', 'prefix', 'runtime'}` at the
top level, and AWS's real botocore.exceptions.ParamValidationError said
that shape is a tagged union of `containerConfiguration` |
`codeConfiguration`, not what we (and the offline tests) had guessed.

This module runs a recorded request through botocore's own ParamValidator,
using whatever botocore version is actually pinned in requirements.txt (the
same one making the real API call), so an offline test can reproduce that
exact ParamValidationError - or prove its absence - with no AWS credentials
and no network access.
"""
from __future__ import annotations

import botocore.session
from botocore.validate import validate_parameters

_session = botocore.session.get_session()


def validate_request(service: str, operation: str, params: dict) -> None:
    """Raise botocore.exceptions.ParamValidationError if `params` does not
    match the real `service` `operation`'s input shape.

    Args:
        service:   A botocore service name, e.g. "bedrock-agentcore-control".
        operation: The operation's CamelCase name, e.g. "CreateAgentRuntime".
        params:    The exact kwargs dict that was (or would be) passed to
                   the boto3 client method - typically read straight out of
                   harness.fakes.recorded[...][-1].
    """
    model = _session.get_service_model(service)
    op = model.operation_model(operation)
    validate_parameters(params, op.input_shape)
