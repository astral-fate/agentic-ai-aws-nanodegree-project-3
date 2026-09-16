import pytest
from harness import bootstrap


@pytest.fixture(scope="session")
def orchestrator():
    mod = bootstrap.load_orchestrator()
    yield mod
    bootstrap.reset()
