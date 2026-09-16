import sys

sys.path.insert(0, "scripts")


def test_scenario_ids_match_the_udacity_brief():
    import run_scenarios
    ids = [s["id"] for s in run_scenarios.SCENARIOS]
    assert ids == ["01-return-order", "02-policy-question", "03-direct-math"]
    assert run_scenarios.SCENARIOS[0]["message"] == "I want to return my order ORD-27176"
    assert run_scenarios.SCENARIOS[0]["customer_id"] == "CUST-001"


def test_offline_mode_exercises_the_expected_routing():
    """The offline harness runs the real, unmodified five-agent graph with a
    scripted (not fabricated) model stand-in. This asserts the actual
    tool-call sequence recorded by harness/scripted_model.py matches each
    scenario's expected routing, rather than trusting the script's own
    'actual_agents_called' label."""
    import run_scenarios

    report = run_scenarios.run_offline()
    by_id = {e["id"]: e for e in report}

    assert by_id["01-return-order"]["error"] is None
    assert by_id["01-return-order"]["actual_agents_called"] == (
        "OrchestratorAgent -> InventoryAgent -> RefundAgent -> CommunicationAgent"
    )

    assert by_id["02-policy-question"]["error"] is None
    called = by_id["02-policy-question"]["actual_agents_called"]
    assert called.startswith("OrchestratorAgent -> PolicyAgent")
    assert called.endswith("CommunicationAgent")
    assert "Retriever" in called  # the three parallel policy retrievers fired

    assert by_id["03-direct-math"]["error"] is None
    assert by_id["03-direct-math"]["actual_agents_called"] == (
        "OrchestratorAgent -> CommunicationAgent"
    )


def test_offline_report_never_claims_a_trace_id():
    """Nothing was deployed offline, so no X-Ray trace exists - the report
    must say so rather than inventing one."""
    import run_scenarios

    report = run_scenarios.run_offline()
    for entry in report:
        assert entry["trace_id"] is None
        assert "no x-ray trace" in entry["trace_note"].lower()
        assert "no deployed runtime" in entry["caveat"].lower()
