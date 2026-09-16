"""The AgentCore Runtime container contract.

AgentCore starts the entryPoint with **no arguments** and expects an HTTP
server on :8080 answering `GET /ping` during initialisation and
`POST /invocations` thereafter. Two defects broke that live, and neither was
visible offline because nothing exercised the container's start-up path:

  1. Every `__main__` branch was guarded by `len(sys.argv) > 1`, so an
     argument-less start matched nothing, printed usage and exited. The
     container never listened.
  2. `_serve_http()` built all five agents *before* binding the socket, so
     even once serve ran, `/ping` could not be answered during init.

Both surfaced identically and misleadingly as:

    RuntimeClientError: Runtime initialization time exceeded.

These tests run the real server in a subprocess under the offline harness and
assert the contract directly.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

SOURCE = "src/agent_orchestrator.py"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_SERVER = """
import sys
sys.path.insert(0, ".")
from harness import bootstrap
mod = bootstrap.load_orchestrator()
mod._serve_http()
"""


@pytest.fixture
def server():
    """Start _serve_http() in a subprocess on a free port; yield its base URL."""
    port = _free_port()
    env = {**os.environ, "PORT": str(port)}
    proc = subprocess.Popen([sys.executable, "-c", _SERVER],
                            env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 90
        while time.time() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"server exited early:\n{proc.stdout.read()}")
            try:
                with urllib.request.urlopen(f"{base}/ping", timeout=2) as r:
                    if r.status == 200:
                        break
            except (urllib.error.URLError, ConnectionError, socket.timeout):
                time.sleep(0.25)
        else:
            pytest.fail("server never answered /ping")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_ping_answers_before_the_agent_graph_is_built(server):
    """/ping must be answerable during init — that is what AgentCore checks.

    The fixture already proves this: it polls /ping and fails if the server
    never answers. This asserts the response shape too.
    """
    with urllib.request.urlopen(f"{server}/ping", timeout=10) as r:
        assert r.status == 200
        assert json.loads(r.read())["status"] == "healthy"


def test_invocations_accepts_the_payload_invoke_agent_sends(server):
    """The body `invoke_agent()` posts must be the body `serve` parses."""
    body = json.dumps({
        "session_id":  "s-contract-test",
        "customer_id": "CUST-001",
        "prompt":      "[Session ID: s-contract-test] [Customer ID: CUST-001] hello",
    }).encode()
    req = urllib.request.Request(f"{server}/invocations", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        assert r.status == 200
        assert "response" in json.loads(r.read())


def test_unknown_paths_404(server):
    req = urllib.request.Request(f"{server}/nope", data=b"{}",
                                 headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=10)
    assert exc.value.code == 404


def test_argumentless_start_is_wired_to_serve():
    """AgentCore runs the entryPoint with no args; that must reach _serve_http.

    Guards against a regression to `if len(sys.argv) > 1` on every branch,
    which made an argument-less start print usage and exit.
    """
    src = open(SOURCE, encoding="utf-8").read()
    main = src.split("if __name__ == '__main__':", 1)[1]
    assert "len(sys.argv) <= 1" in main, "no argument-less branch in __main__"
    branch = main.split("len(sys.argv) <= 1", 1)[1].split("else:", 1)[0]
    assert "_serve_http()" in branch, "the argument-less branch does not serve"
