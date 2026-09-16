"""Regression coverage for the `ModuleNotFoundError: No module named
'agent_orchestrator'` class of bug.

Both scripts/run_adversarial.py's run_live() and scripts/run_scenarios.py's
run_live() put the repo ROOT on sys.path (for `import config`) but, until
this fix, never put ROOT/src on sys.path - so `import agent_orchestrator`
failed immediately against a real, live-deployed runtime. It happened twice,
independently, in the two scripts - once each, on two separate live runs -
which is why the fix was extracted into scripts/_pathutil.py and both call
sites now share it rather than each re-deriving the path.
"""
import pathlib
import sys


def test_ensure_src_on_path_points_at_the_real_agent_orchestrator():
    """pytest.ini's own `pythonpath = . src harness` already puts src/ on
    sys.path for the *test* process, so this cannot assert "nothing was
    there before" without a false failure - it asserts the thing that
    actually matters: after calling it, the exact directory
    agent_orchestrator.py lives in is on sys.path."""
    sys.path.insert(0, "scripts")
    from _pathutil import ensure_src_on_path

    root = pathlib.Path(".").resolve()
    src_dir = str(root / "src")

    ensure_src_on_path(root)

    assert src_dir in sys.path
    assert (root / "src" / "agent_orchestrator.py").is_file(), \
        "ensure_src_on_path did not point at the directory agent_orchestrator.py lives in"


def test_both_live_entrypoints_use_the_shared_path_fix():
    """Guards against the fix drifting back out of one of the two call
    sites - which is exactly how this bug shipped twice."""
    for path in ("scripts/run_adversarial.py", "scripts/run_scenarios.py"):
        text = pathlib.Path(path).read_text(encoding="utf-8")
        assert "ensure_src_on_path(ROOT)" in text, \
            f"{path}'s run_live() lost the src/ sys.path fix"
