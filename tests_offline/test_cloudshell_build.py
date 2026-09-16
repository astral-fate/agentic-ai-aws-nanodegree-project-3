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
