def test_orchestrator_imports_with_no_aws(orchestrator):
    """The unmodified deliverable imports with no credentials and no network."""
    assert hasattr(orchestrator, "build_inventory_agent")
    assert hasattr(orchestrator, "_update_workflow_state")


def test_workflow_state_table_is_real_moto(orchestrator):
    """WorkflowState goes to a real moto DynamoDB table, not a dict."""
    state = orchestrator._create_workflow_state("s-boot", "CUST-001")
    assert state["version"] == 0
    assert orchestrator._read_workflow_state("s-boot")["customer_id"] == "CUST-001"


def test_deliverable_file_is_untouched(orchestrator):
    """We test the graded file in place - never a copy."""
    import pathlib
    assert pathlib.Path(orchestrator.__file__).resolve() == \
           pathlib.Path("src/agent_orchestrator.py").resolve()
