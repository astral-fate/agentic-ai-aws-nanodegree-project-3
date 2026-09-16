"""
harness/fakes.py
================
Stand-ins registered in sys.modules before the deliverable is imported.

These are deliberately uneven. Tool registration, agent wiring and the
ThreadPoolExecutor fan-out are real; model inference is scripted. That split is
what the evidence claims and nothing more.
"""
import sys
import types


class _ToolRegistry:
    """Mirrors strands' ToolRegistry: an object with a .registry dict.

    tests/test_agent.py introspects exactly this shape, so the harness must
    match it or our offline tool-count checks would prove nothing about the
    real grader.
    """
    def __init__(self):
        self.registry = {}


class FakeAgent:
    def __init__(self, model=None, system_prompt="", tools=None, name=None, **kwargs):
        self.model = model
        self.system_prompt = system_prompt
        self.name = name
        self.tool_registry = _ToolRegistry()
        for fn in (tools or []):
            self.tool_registry.registry[getattr(fn, "__name__", repr(fn))] = fn
        self._responder = None

    def __call__(self, prompt, **kwargs):
        from harness.scripted_model import respond
        return respond(self, prompt)


class FakeBedrockModel:
    def __init__(self, model_id=None, temperature=None, **kwargs):
        # test_agent.py reads model.config['model_id'] - match that shape.
        self.config = {"model_id": model_id, "temperature": temperature}
        self.model_id = model_id
        self.temperature = temperature


def _tool(*args, **kwargs):
    """Stand-in for strands' @tool: marks the function and returns it unchanged.

    Supports both bare-decorator (`@tool`) and call form (`@tool(name=...)`),
    matching the real strands.tool's calling conventions.
    """
    def _mark(fn):
        fn.__is_tool__ = True
        return fn

    if len(args) == 1 and callable(args[0]) and not kwargs:
        return _mark(args[0])
    return _mark


def register():
    """Put the stand-ins in sys.modules. Must run before importing the deliverable.

    This must run even though the real `strands-agents` PyPI package is
    installed (it is a listed dependency in requirements.txt): Python's
    import system checks sys.modules first, so pre-populating "strands" /
    "strands.models" here means the deliverable's `from strands import
    Agent, tool` and `from strands.models import BedrockModel` resolve to
    these fakes instead of ever importing the real package.
    """
    strands = types.ModuleType("strands")
    strands.Agent = FakeAgent
    strands.tool = _tool

    models = types.ModuleType("strands.models")
    models.BedrockModel = FakeBedrockModel

    strands.models = models
    sys.modules["strands"] = strands
    sys.modules["strands.models"] = models


recorded: dict[str, list] = {}


class _StubClient:
    """Records every call and returns a plausible shape.

    Control-plane calls (guardrail, runtime, memory) are asserted on their
    REQUEST payload, which is what the rubric specifies. Their responses here
    are fabricated and prove nothing about AWS.
    """
    def __init__(self, service):
        self._service = service

    def __getattr__(self, op):
        def _op(**kwargs):
            recorded.setdefault(op, []).append(kwargs)
            return _RESPONSES.get(op, {})
        return _op


_RESPONSES = {
    "create_guardrail":        {"guardrailId": "gr-offline-001", "version": "DRAFT"},
    "create_guardrail_version": {"version": "1"},
    "create_agent_runtime":    {"agentRuntimeArn":
                                "arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/offline"},
    "create_memory":           {"memory": {"memoryArn":
                                "arn:aws:bedrock-agentcore:us-east-1:000000000000:memory/offline",
                                "status": "ACTIVE"}},
    "get_memory":              {"memory": {"status": "ACTIVE"}},
}

_STUBBED = {"bedrock", "bedrock-agent", "bedrock-runtime",
            "bedrock-agentcore", "bedrock-agentcore-control", "xray"}


def register_boto_stubs():
    """Route the services moto does not emulate to _StubClient; leave the rest to moto.

    moto 5.2.3 does implement partial backends for "bedrock",
    "bedrock-agent", "bedrock-runtime" and "bedrock-agentcore-control", but
    none of them implement guardrail creation, and Tasks 9/10 need every
    control-plane request payload captured on `recorded` regardless of
    whether moto happens to understand the operation. So these five
    services (plus "xray", whose trace-submission surface moto only
    partially covers) are routed to `_StubClient` unconditionally, rather
    than falling back to moto only where moto is incomplete.
    """
    import boto3
    real_client = boto3.client

    def client(service, *args, **kwargs):
        if service in _STUBBED:
            return _StubClient(service)
        return real_client(service, *args, **kwargs)

    boto3.client = client


def patch_kb_retrieval():
    """Point retrieve_from_knowledge_base at the fixtures.

    Patching the module attribute (not editing the file) keeps the
    do-not-modify starter untouched. This must run after `src` is on
    sys.path (so `import bedrock_kb_retrieval` resolves) and before
    `import agent_orchestrator` - the deliverable does `from
    bedrock_kb_retrieval import retrieve_from_knowledge_base`, which binds
    the name into agent_orchestrator's own namespace at import time.
    Patching the bedrock_kb_retrieval module after agent_orchestrator is
    already imported would leave agent_orchestrator's bound name pointing at
    the original (real-Bedrock-calling) function.
    """
    import bedrock_kb_retrieval
    from harness import kb_fixtures
    bedrock_kb_retrieval.retrieve_from_knowledge_base = kb_fixtures.passages
