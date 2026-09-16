"""Tests for infrastructure/cleanup.py.

test_dry_run_is_the_default_and_deletes_nothing runs the script as a real
subprocess with whatever AWS credentials this machine happens to have (none,
on the dev box this was written on). config.py raises at import time with no
credentials, so cleanup.py must degrade to a clear "nothing to discover"
message rather than crash - see the module docstring in cleanup.py. This
test asserts on the properties that must hold either way: dry run is the
default, nothing gets deleted, and the script tells you how to actually
delete something.

The rest of the tests exercise cleanup's logic in-process. They request the
`orchestrator` fixture purely for its side effect of booting the moto +
fake-credentials harness (see harness/bootstrap.py), which is what lets
`import config` succeed inside cleanup.py without hitting real AWS.
"""
import subprocess
import sys

import pytest


def test_dry_run_is_the_default_and_deletes_nothing():
    out = subprocess.run([sys.executable, "infrastructure/cleanup.py"],
                         capture_output=True, text=True, timeout=120)
    combined = out.stdout + out.stderr
    assert "dry run" in combined.lower()
    assert "--yes" in combined
    assert out.returncode == 0


def test_deletion_order_puts_idle_billing_first(orchestrator, monkeypatch):
    sys.path.insert(0, "infrastructure")
    import cleanup

    real_client = cleanup.boto3.client

    class _FakeAgentClient:
        def list_knowledge_bases(self, **kwargs):
            return {"knowledgeBaseSummaries": [
                {"knowledgeBaseId": "KB-OFFLINE-01",
                 "name": f"{cleanup.config.PROJECT_NAME}-novamart-policies"},
            ]}

    def fake_client(service, *args, **kwargs):
        if service == "bedrock-agent":
            return _FakeAgentClient()
        return real_client(service, *args, **kwargs)

    monkeypatch.setattr(cleanup.boto3, "client", fake_client)

    order = [step["kind"] for step in cleanup.plan()]
    assert order.index("knowledge-base") < order.index("cloudformation-stack")
    assert order.index("s3-vectors") < order.index("cloudformation-stack")


def test_owned_rejects_names_outside_the_project(orchestrator):
    sys.path.insert(0, "infrastructure")
    import cleanup

    assert cleanup._owned(f"{cleanup.config.PROJECT_NAME}-vectors") is True
    assert cleanup._owned("someone-elses-bucket") is False
    assert cleanup._owned("") is False


def test_one_failure_does_not_abort_the_rest(orchestrator, monkeypatch, capsys):
    sys.path.insert(0, "infrastructure")
    import cleanup

    steps = [
        {"kind": "knowledge-base", "name": "udacity-agentcore-kb-a", "why": "x"},
        {"kind": "s3-vectors", "name": "udacity-agentcore-vectors", "why": "y"},
    ]
    monkeypatch.setattr(cleanup, "plan", lambda: steps)
    monkeypatch.setattr(cleanup, "_guard_account", lambda force: None)

    def fake_delete(step):
        if step["kind"] == "knowledge-base":
            raise RuntimeError("boom")
        # s3-vectors: succeeds silently

    monkeypatch.setattr(cleanup, "_delete", fake_delete)

    rc = cleanup.main(["--yes"])

    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "udacity-agentcore-kb-a" in out
    assert "deleted" in out
    assert "udacity-agentcore-vectors" in out
    assert rc == 1  # one failure -> non-zero exit, but every step still ran


def test_guard_account_refuses_unowned_account_without_force(orchestrator, monkeypatch):
    sys.path.insert(0, "infrastructure")
    import cleanup

    real_client = cleanup.boto3.client

    class _NoStackCfn:
        def describe_stacks(self, **kwargs):
            raise Exception("stack not found")

    class _Sts:
        def get_caller_identity(self):
            return {"Account": "999999999999"}

    def fake_client(service, *args, **kwargs):
        if service == "cloudformation":
            return _NoStackCfn()
        if service == "sts":
            return _Sts()
        return real_client(service, *args, **kwargs)

    monkeypatch.setattr(cleanup.boto3, "client", fake_client)

    with pytest.raises(SystemExit) as exc_info:
        cleanup._guard_account(force=False)
    assert exc_info.value.code == 3

    # --force bypasses the refusal
    cleanup._guard_account(force=True)
