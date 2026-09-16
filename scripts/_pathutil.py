"""Shared helper for scripts/ entry points that import agent_orchestrator
directly against a live deployment.

Both run_adversarial.py's run_live() and run_scenarios.py's run_live() do
`import config` (satisfied by inserting the repo ROOT onto sys.path, which
both already did) followed by `import agent_orchestrator` (which lives in
src/, a directory ROOT does not cover). That second import was missing its
own sys.path entry in both scripts, independently, and cost two separate
live CloudShell runs the identical `ModuleNotFoundError: No module named
'agent_orchestrator'` before either was noticed. Extracted here once so the
fix cannot drift back out of sync between the two call sites - or apply to
only one of them again if a third script needs it later.
"""
from __future__ import annotations

import pathlib
import sys


def ensure_src_on_path(root: pathlib.Path) -> None:
    """Insert <root>/src onto sys.path, once, so `import agent_orchestrator`
    (and anything else that lives in src/) resolves."""
    src_dir = root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
