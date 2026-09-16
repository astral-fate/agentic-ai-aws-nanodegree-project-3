import pathlib
import subprocess
import sys


def test_generator_embeds_every_project_file(tmp_path):
    out = tmp_path / "deploy-e2e-test.sh"
    subprocess.run([sys.executable, "scripts/build_cloudshell_script.py",
                    "--out", str(out)], check=True, timeout=120)
    text = out.read_text(encoding="utf-8")
    for needed in ("agent_orchestrator.py", "config.py", "test_agent.py",
                   "starter_stack.yaml", "seed_data.py", "cleanup.py"):
        assert needed in text, f"{needed} was not embedded"
    assert "__EMBEDDED" + "_FILES__" not in text, "placeholder was never substituted"


def test_generated_script_is_valid_bash(tmp_path):
    out = tmp_path / "deploy-e2e-test.sh"
    subprocess.run([sys.executable, "scripts/build_cloudshell_script.py",
                    "--out", str(out)], check=True, timeout=120)
    # Fed via stdin rather than as a path argument. On Windows, MSYS2's bash
    # mis-parses argv when it is spawned by a non-MSYS parent (here, a native
    # python.exe running pytest) — a documented MSYS/Cygwin quirk where the
    # runtime's own argv reconstruction only works when the parent is itself
    # Cygwin/MSYS-aware. It silently drops or mangles path arguments (e.g.
    # backslashes vanish entirely), which is unrelated to whether the
    # generated script is valid bash. `bash -n` with no file operand reads
    # the script from stdin instead, sidestepping the argv path entirely.
    subprocess.run(["bash", "-n"], input=out.read_bytes(), check=True, timeout=60)


def test_generated_script_has_unix_line_endings(tmp_path):
    out = tmp_path / "deploy-e2e-test.sh"
    subprocess.run([sys.executable, "scripts/build_cloudshell_script.py",
                    "--out", str(out)], check=True, timeout=120)
    assert b"\r\n" not in out.read_bytes(), \
        "CRLF in a bash script fails in CloudShell with $'\\r': command not found"


def test_template_refuses_to_run_directly():
    text = pathlib.Path("cloudshell/_deploy-e2e.template.sh").read_text(encoding="utf-8")
    assert "This is the template, not the runnable script" in text


def test_score_parser_strips_ansi_before_matching():
    """Regression: tests/test_agent.py's print_score() wraps the score line
    in ANSI colour codes - Colors.BOLD before "Score:" and a colour code
    between "Score: " and the digits (see tests/test_agent.py:509-513) - so
    the digits are never adjacent to the literal text "Score: " in the raw
    captured bytes. A perfect 120/120 run was reported as PARTIAL/"no score
    line found" in the summary table until run_grader() in
    cloudshell/_deploy-e2e.template.sh stripped ANSI before matching. This
    feeds the exact byte sequence print_score() emits for a 100% run through
    that same sed+grep pipeline.

    The sample is piped in over stdin rather than passed as a file path
    argument: a Windows tmp_path contains backslashes, and bash.exe spawned
    directly by a native python.exe (not another MSYS/Cygwin process)
    mis-parses argv and can mangle them - the same documented quirk
    test_generated_script_is_valid_bash above works around the same way.
    """
    sample = b"  \x1b[1mScore: \x1b[92m120/120 pts (100%)\x1b[0m\n"
    plain_pattern = r"Score: [0-9]+/[0-9]+ pts \([0-9]+%\)"

    # The old, un-stripped grep must fail on this input - proving this is a
    # real regression test, not a vacuous one.
    old = subprocess.run(
        ["bash", "-c", f"grep -oE '{plain_pattern}'"],
        input=sample, capture_output=True, timeout=10,
    )
    assert old.stdout.strip() == b"", \
        "the un-stripped grep should not match a colourised score line"

    # The fixed pipeline (mirrors run_grader()'s score_line extraction).
    fixed = subprocess.run(
        ["bash", "-c",
         f"sed -r 's/\\x1B\\[[0-9;]*[mK]//g' | grep -oE '{plain_pattern}' | tail -1"],
        input=sample, capture_output=True, timeout=10,
    )
    assert fixed.stdout.strip() == b"Score: 120/120 pts (100%)"


def test_score_parser_pipeline_matches_the_template_verbatim():
    """The sed/grep pipeline above must be the one actually shipped, not a
    copy that could drift from cloudshell/_deploy-e2e.template.sh."""
    text = pathlib.Path("cloudshell/_deploy-e2e.template.sh").read_text(encoding="utf-8")
    assert r"sed -r 's/\x1B\[[0-9;]*[mK]//g'" in text
    assert "score_line=" in text
