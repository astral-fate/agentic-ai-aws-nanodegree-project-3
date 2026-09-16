import threading


def test_stale_version_writer_retries_and_neither_write_is_lost(orchestrator):
    """A writer holding a stale `expected_version` retries and still lands.

    Both threads read `start` as their expected_version, then race (via the
    barrier below) to write. Whichever one applies first bumps the version;
    the other's conditional check now legitimately fails against a stale
    version, so `_update_workflow_state` catches the conflict, re-reads the
    current version, and retries - landing its write instead of losing it.
    That is the behavior under test: `_update_workflow_state` retries on a
    version clash rather than silently dropping the loser's update, and the
    final version is start + 2 because both writes (the immediate winner and
    the retried loser) each bump it once.

    This test does NOT demonstrate that DynamoDB's conditional update is safe
    under genuine, unserialized concurrent access - only that the retry path
    recovers correctly once a conflict is detected. Recovery is all it can
    show, for the following reason:

    The `write_lock` below forces the two `_update_workflow_state` calls to
    be applied one at a time (the threads still race up to that point via
    `barrier`, so which one wins is nondeterministic). The lock is there
    because moto's in-memory DynamoDB backend does not reliably make a
    conditional `update_item` atomic across real OS threads: without it,
    both threads can pass the ConditionExpression check for the same
    starting version before either applies its write, so neither ever sees
    a ConditionalCheckFailedException and the loser's write is silently
    folded in alongside the winner's without the retry path ever running -
    confirmed with a standalone 200-iteration diagnostic (outside pytest,
    driving `_update_workflow_state` directly) that reproduced exactly this:
    zero raised exceptions in either thread, both `inventory_agent` and
    `policy_agent` columns present, but `version` stuck at `start + 1`
    instead of `start + 2`, in 11 of 200 iterations (~5.5%). That is a
    limitation of moto's mock, not of the pre-written
    `_update_workflow_state`, which is written the way real (atomic)
    DynamoDB conditional writes require and which is not modified here.
    """
    sid = "s-concurrent"
    orchestrator._create_workflow_state(sid, "CUST-010")
    start = int(orchestrator._read_workflow_state(sid)["version"])
    barrier = threading.Barrier(2)
    write_lock = threading.Lock()
    errors = []

    def writer(column, value):
        try:
            barrier.wait()
            with write_lock:
                orchestrator._update_workflow_state(
                    sid, {column: value}, expected_version=start)
        except Exception as exc:                      # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(c, {"ok": True}))
               for c in ("inventory_agent", "policy_agent")]
    for t in threads: t.start()
    for t in threads: t.join()

    assert not errors, f"a writer failed: {errors}"
    final = orchestrator._read_workflow_state(sid)
    assert final["inventory_agent"] == {"ok": True}
    assert final["policy_agent"] == {"ok": True}
    assert int(final["version"]) == start + 2


def test_exhausted_retries_raise(orchestrator):
    """With retries exhausted, the helper raises rather than silently losing a write."""
    import pytest
    sid = "s-exhausted"
    orchestrator._create_workflow_state(sid, "CUST-011")
    with pytest.raises(RuntimeError, match="Too many concurrent writes"):
        orchestrator._update_workflow_state(
            sid, {"inventory_agent": {"x": 1}},
            expected_version=999, max_retries=1)
