import time
import pytest


@pytest.fixture
def policy(orchestrator):
    return orchestrator.build_policy_agent()


def test_coordinator_has_exactly_one_tool(policy):
    assert set(policy.tool_registry.registry) == {"search_all_policies"}


def test_coordinator_temperature_is_point_two(policy):
    import config
    assert policy.model.config["model_id"] == config.WORKER_MODEL_ID
    assert policy.model.config["temperature"] == 0.2


def test_returns_results_from_all_three_knowledge_bases(policy):
    """Each domain's result must contain content unique to its own fixture -
    not merely be non-empty, which an error string or a refusal message
    would also satisfy against the live SDK."""
    out = policy.tool_registry.registry["search_all_policies"](
        query="What is the return policy for premium customers?")
    assert set(out["results"]) == {"returns", "shipping", "warranty"}
    assert "30 days of delivery" in out["results"]["returns"]
    assert "two-day shipping" in out["results"]["shipping"]
    assert "12-month limited warranty" in out["results"]["warranty"]


def test_search_all_policies_actually_invokes_each_retriever_agent(orchestrator, policy):
    """Guards against silently reinstating the pre-fix bypass, where
    search_all_policies called retrieve_from_knowledge_base directly instead
    of invoking the retriever sub-agents. harness/scripted_model.py records
    every (agent_name, tool_name) call it dispatches, so if the retriever
    agents are never called, none of their names show up here."""
    from harness import scripted_model

    scripted_model.reset_calls()
    policy.tool_registry.registry["search_all_policies"](query="returns")
    called_agents = {agent_name for agent_name, _tool_name in scripted_model.calls}
    assert called_agents == {
        "ReturnsPolicyRetrieverAgent",
        "ShippingPolicyRetrieverAgent",
        "WarrantyPolicyRetrieverAgent",
    }


def test_retrievals_actually_overlap_in_time(orchestrator, policy, monkeypatch):
    """ThreadPoolExecutor(max_workers=3) must fan out, not run serially.

    Each fixture call sleeps 200ms. Serial would take >=600ms; parallel well
    under. The suite has no test for this, so it lives here.
    """
    import bedrock_kb_retrieval
    from harness import kb_fixtures

    def slow(kb_id, query="", top_k=3):
        time.sleep(0.2)
        return kb_fixtures.passages(kb_id, query, top_k)

    monkeypatch.setattr(bedrock_kb_retrieval, "retrieve_from_knowledge_base", slow)
    monkeypatch.setattr(orchestrator, "retrieve_from_knowledge_base", slow)

    start = time.perf_counter()
    policy.tool_registry.registry["search_all_policies"](query="returns")
    elapsed = time.perf_counter() - start
    assert elapsed < 0.45, f"retrievals appear serial ({elapsed:.2f}s)"


def test_one_failing_retriever_does_not_lose_the_others(orchestrator, policy, monkeypatch):
    import bedrock_kb_retrieval
    from harness import kb_fixtures
    import config

    def flaky(kb_id, query="", top_k=3):
        if kb_id == config.SHIPPING_KB_ID:
            raise RuntimeError("shipping KB unavailable")
        return kb_fixtures.passages(kb_id, query, top_k)

    monkeypatch.setattr(bedrock_kb_retrieval, "retrieve_from_knowledge_base", flaky)
    monkeypatch.setattr(orchestrator, "retrieve_from_knowledge_base", flaky)

    out = policy.tool_registry.registry["search_all_policies"](query="returns")
    assert out["results"]["returns"], "a sibling failure lost the returns results"
    assert out["results"]["warranty"], "a sibling failure lost the warranty results"
    assert "shipping" in out["errors"]
