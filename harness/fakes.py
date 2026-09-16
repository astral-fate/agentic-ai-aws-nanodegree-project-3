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


def register_boto_stubs():
    """Placeholder for Task 3.

    Task 3 will replace this with real stubs for the Bedrock / AgentCore
    control-plane calls (bedrock-agent, bedrock-agentcore,
    bedrock-agentcore-control, etc.) that moto cannot emulate on its own.
    For Task 2's purposes this is a deliberate no-op: nothing at
    `agent_orchestrator` import time actually *calls* those clients (they
    are only constructed and have compat event-hooks registered against
    them), so no stub is required yet for the module to import cleanly.
    """
    pass
