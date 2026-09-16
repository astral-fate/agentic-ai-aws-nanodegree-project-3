"""
Build the self-contained CloudShell deploy script.

    python scripts/build_cloudshell_script.py [--out PATH]

Reads `cloudshell/_deploy-e2e.template.sh` and replaces the
`__EMBEDDED_FILES__` marker with quoted heredocs carrying the real contents of
every file the deploy needs — config.py, requirements.txt, the five src/
modules, tests/test_agent.py and the three infrastructure/ scripts. The
result needs no clone and no network beyond AWS itself.

Generating it rather than maintaining it by hand is the point: the embedded
copies cannot drift from the repo, because they are re-read on every build
and the build is verified in tests_offline/test_cloudshell_build.py.

Heredocs are quoted (`<<'SENTINEL'`) so nothing inside is expanded —
src/agent_orchestrator.py is full of `$`, backticks and `{}` that bash would
otherwise mangle. Each sentinel is checked against the file's own content so
a collision fails the build loudly instead of producing a script that
silently truncates.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "cloudshell" / "_deploy-e2e.template.sh"
MARKER = "__EMBEDDED_FILES__"

# (source path relative to ROOT, destination path inside $PROJECT_DIR, heredoc sentinel)
FILES: List[Tuple[str, str, str]] = [
    ("config.py", "config.py", "CONFIG_PY_EOF"),
    ("requirements.txt", "requirements.txt", "REQUIREMENTS_TXT_EOF"),
    ("src/agent_orchestrator.py", "src/agent_orchestrator.py", "AGENT_ORCHESTRATOR_PY_EOF"),
    ("src/agent_utils.py", "src/agent_utils.py", "AGENT_UTILS_PY_EOF"),
    ("src/agent_observability.py", "src/agent_observability.py", "AGENT_OBSERVABILITY_PY_EOF"),
    ("src/bedrock_kb_retrieval.py", "src/bedrock_kb_retrieval.py", "BEDROCK_KB_RETRIEVAL_PY_EOF"),
    ("src/demo.py", "src/demo.py", "DEMO_PY_EOF"),
    ("tests/test_agent.py", "tests/test_agent.py", "TEST_AGENT_PY_EOF"),
    ("infrastructure/starter_stack.yaml", "infrastructure/starter_stack.yaml", "STARTER_STACK_YAML_EOF"),
    ("infrastructure/seed_data.py", "infrastructure/seed_data.py", "SEED_DATA_PY_EOF"),
    ("infrastructure/cleanup.py", "infrastructure/cleanup.py", "CLEANUP_PY_EOF"),
    ("scripts/run_adversarial.py", "scripts/run_adversarial.py", "RUN_ADVERSARIAL_PY_EOF"),
    ("scripts/run_scenarios.py", "scripts/run_scenarios.py", "RUN_SCENARIOS_PY_EOF"),
]

INDENT = "  "


def version() -> str:
    """Read SCRIPT_VERSION out of the template — it is the single source."""
    match = re.search(
        r'^SCRIPT_VERSION="([^"]+)"', TEMPLATE.read_text(encoding="utf-8"), re.M
    )
    if not match:
        raise SystemExit("FATAL: SCRIPT_VERSION not found in the template")
    return match.group(1)


def default_output_path() -> Path:
    """
    Versioned filename, e.g. deploy-e2e-v01.sh.

    The version is in the name because the script is uploaded to CloudShell
    by hand as well as pasted directly. Two files differing only by content,
    sitting in the same directory, is exactly how a stale copy gets run.
    """
    return ROOT / "cloudshell" / f"deploy-e2e-{version()}.sh"


def embed(source: Path, dest: str, sentinel: str) -> str:
    """Render one file as an indented, quoted heredoc."""
    content = source.read_text(encoding="utf-8")

    # A line equal to the sentinel would end the heredoc early and the rest
    # of the file would be interpreted as bash. Fail the build rather than
    # ship a script that silently truncates.
    for number, line in enumerate(content.splitlines(), start=1):
        if line.strip() == sentinel:
            raise SystemExit(
                f"FATAL: {source} line {number} collides with heredoc sentinel "
                f"{sentinel!r}. Change the sentinel in FILES."
            )

    if not content.endswith("\n"):
        content += "\n"

    # The heredoc body is written unindented (no <<-): <<- only strips tabs,
    # and these files are space-indented — stripping would corrupt them.
    return (
        f'{INDENT}mkdir -p "$(dirname "$PROJECT_DIR/{dest}")"\n'
        f'{INDENT}cat > "$PROJECT_DIR/{dest}" <<\'{sentinel}\'\n'
        f"{content}"
        f"{sentinel}\n"
    )


def build() -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    if MARKER not in template:
        raise SystemExit(f"FATAL: {MARKER} not found in {TEMPLATE}")

    blocks = []
    for source_rel, dest, sentinel in FILES:
        source = ROOT / source_rel
        if not source.exists():
            raise SystemExit(f"FATAL: missing {source}")
        blocks.append(embed(source, dest, sentinel))

    return template.replace(MARKER, "\n".join(blocks))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Output path. Defaults to cloudshell/deploy-e2e-<version>.sh.",
    )
    args = parser.parse_args(argv)

    script = build()
    out_path = args.out if args.out is not None else default_output_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # newline='\n' forces LF line endings regardless of platform — a CRLF
    # bash script fails in CloudShell with a confusing $'\r': command not found.
    out_path.write_text(script, encoding="utf-8", newline="\n")
    try:
        out_path.chmod(0o755)  # executable bit, for the platforms that honour it
    except OSError:
        pass

    lines = script.count("\n")
    size_kb = len(script.encode("utf-8")) / 1024
    try:
        rel = out_path.relative_to(ROOT).as_posix()
    except ValueError:
        rel = str(out_path)
    print(f"wrote {rel}  ({lines:,} lines, {size_kb:.0f} KB, version {version()})")
    print("embedded:")
    for source_rel, dest, _ in FILES:
        n = (ROOT / source_rel).read_text(encoding="utf-8").count("\n")
        print(f"  {dest:<38} {n:>4} lines   from {source_rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
