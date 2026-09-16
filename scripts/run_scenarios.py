#!/usr/bin/env python3
"""Run the three Udacity brief scenarios and write one transcript each.

    python scripts/run_scenarios.py --offline   # in-process harness, no AWS
    python scripts/run_scenarios.py --live      # real deployed runtime

Scenarios (fixed, from the Udacity brief):

  1. "I want to return my order ORD-27176" as CUST-001
       -> Orchestrator -> Inventory -> Refund -> Communication
  2. "What is the return policy for premium customers?"
       -> Orchestrator -> Policy (3 parallel retrievers) -> Communication
  3. "How much are 5 items at $29.99 with 10% off?"
       -> Orchestrator answers directly (no worker routing)

--offline and --live are two different claims, kept as separate as
run_adversarial.py keeps its own two modes:

  --offline  Boots the harness + fake-strands stand-in
             (harness/bootstrap.py), builds the real, unmodified five-agent
             graph, and calls the orchestrator **in-process** - no network,
             no deployed runtime. harness/scripted_model.py is a rule-based
             stand-in for the LLM (regex/keyword routing, not a model
             decision), so this proves the tool wiring and WorkflowState
             threading are right for these three prompts, never that a real
             model would route them the same way. There is no X-Ray trace to
             look up offline - nothing was deployed - so the transcript says
             exactly that instead of inventing a trace id.

  --live     Calls agent_orchestrator.invoke_agent() - unmodified,
             pre-written - against a real deployed AgentCore Runtime, then
             looks up the matching AWS X-Ray trace.

This machine has no AWS credentials and nothing deployed (see MEMORY.md), so
--offline is the only mode that has actually been run here. --live only
works from a real session with a deployed runtime, wired into
cloudshell/_deploy-e2e.template.sh.

X-Ray trace lookup (--live only)
---------------------------------
agent_orchestrator.invoke_agent() (pre-written, not modified here) only
returns the assembled response text - no trace id travels back over that
API call, and the actual segment is written *inside* the running
AgentCore Runtime container, which this process cannot read directly.

So the trace id is looked up the only honest way available from outside the
container: AWS X-Ray's GetTraceSummaries, restricted to the time window this
scenario's call actually ran in, polled with the same ingestion-delay
patience as capture_console.py's Service Map shot (traces take 30-60s to
appear). This is a **time-window** lookup, not a targeted one: the code that
builds the real X-Ray segments observed live (AgentCore Runtime's own
auto-instrumentation via CloudWatch Transaction Search, configured by
agent_orchestrator.configure_observability()) was never exercised against
real AWS from this machine, so the exact segment/service name it uses in
practice is unverified. If GetTraceSummaries returns more than one trace in
a scenario's window, all of them are reported - never silently narrowed to a
guess - so a human can correlate by timestamp against the transcript.
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ─────────────────────────────────────────────────────────────
# THE FIXED SCENARIO LIST (verbatim from the Udacity brief)
# ─────────────────────────────────────────────────────────────
SCENARIOS = [
    {
        "id": "01-return-order",
        "customer_id": "CUST-001",
        "message": "I want to return my order ORD-27176",
        "expected_routing": "Orchestrator -> Inventory -> Refund -> Communication",
    },
    {
        "id": "02-policy-question",
        "customer_id": "CUST-002",
        "message": "What is the return policy for premium customers?",
        "expected_routing": "Orchestrator -> Policy (3 parallel retrievers: "
                            "returns, shipping, warranty) -> Communication",
    },
    {
        "id": "03-direct-math",
        "customer_id": "CUST-003",
        "message": "How much are 5 items at $29.99 with 10% off?",
        "expected_routing": "Orchestrator answers directly - no worker routing",
    },
]

_OFFLINE_CAVEAT = (
    "Offline mode: this ran the real, unmodified five-agent graph in-process "
    "against harness/scripted_model.py, a rule-based (not an LLM) stand-in "
    "for the model. It shows the tool wiring and routing rules are correct "
    "for this prompt - it does not show a real model would route it the "
    "same way, and there is no deployed runtime, so no X-Ray trace exists."
)

_LIVE_CAVEAT = (
    "Live mode: this is the actual response from invoke_agent() against the "
    "deployed AgentCore Runtime. The X-Ray trace id below (if any) comes "
    "from a GetTraceSummaries lookup over this call's time window, not from "
    "a targeted trace id returned by invoke_agent_runtime() itself - see the "
    "module docstring for why that lookup can't be more precise than a "
    "time window from outside the container."
)


# ─────────────────────────────────────────────────────────────
# OFFLINE MODE - in-process, no AWS
# ─────────────────────────────────────────────────────────────

def run_offline() -> list[dict]:
    """Build the real five-agent graph in-process (harness/bootstrap.py) and
    run each scenario through it directly, exactly like
    `agent_orchestrator.py test` does. No invoke_agent(), no AWS network
    call - this only proves the in-process wiring."""
    from harness import bootstrap, scripted_model

    orchestrator_module = bootstrap.load_orchestrator()

    inventory_agent     = orchestrator_module.build_inventory_agent()
    refund_agent        = orchestrator_module.build_refund_agent()
    policy_agent        = orchestrator_module.build_policy_agent()
    communication_agent = orchestrator_module.build_communication_agent()
    orchestrator = orchestrator_module.build_orchestrator_agent(
        inventory_agent, refund_agent, policy_agent, communication_agent
    )

    report = []
    for scenario in SCENARIOS:
        session_id = f"s-offline-{uuid.uuid4().hex[:8]}"
        scripted_model.reset_calls()
        prompt = (f"[Session ID: {session_id}] "
                  f"[Customer ID: {scenario['customer_id']}] {scenario['message']}")
        try:
            response = str(orchestrator(prompt))
            calls = list(scripted_model.calls)
            error = None
        except Exception as exc:  # noqa: BLE001 - one scenario must not lose the rest
            response, calls, error = "", [], str(exc)

        agents_called = " -> ".join(dict.fromkeys(name for name, _tool in calls)) or "(none)"
        report.append({
            "id": scenario["id"],
            "session_id": session_id,
            "customer_id": scenario["customer_id"],
            "message": scenario["message"],
            "expected_routing": scenario["expected_routing"],
            "response": response,
            "actual_agents_called": agents_called,
            "tool_calls": [f"{agent}.{tool_name}" for agent, tool_name in calls],
            "trace_id": None,
            "trace_note": "offline: nothing deployed, no X-Ray trace exists",
            "error": error,
            "caveat": _OFFLINE_CAVEAT,
        })
    return report


# ─────────────────────────────────────────────────────────────
# LIVE MODE - a real invocation of the deployed runtime
# ─────────────────────────────────────────────────────────────

def _lookup_xray_trace_ids(start_ts: float, end_ts: float, region: str,
                            wait: int, poll_interval: int = 10) -> tuple[list[str], str]:
    """Poll AWS X-Ray for traces whose events fall in [start_ts, end_ts].

    Returns (trace_ids, note). Never raises - a missing/misconfigured X-Ray
    client is reported in `note`, not fabricated as an empty-but-successful
    result.
    """
    try:
        import boto3
    except ImportError:
        return [], "boto3 not available - cannot query X-Ray"

    try:
        xray = boto3.client("xray", region_name=region)
    except Exception as exc:  # noqa: BLE001
        return [], f"could not create an X-Ray client: {exc}"

    deadline = time.time() + wait
    last_note = ""
    while True:
        try:
            resp = xray.get_trace_summaries(
                StartTime=dt.datetime.utcfromtimestamp(start_ts - 5),
                EndTime=dt.datetime.utcfromtimestamp(max(end_ts, time.time()) + 1),
                TimeRangeType="Event",
            )
            summaries = resp.get("TraceSummaries", [])
            ids = [s["Id"] for s in summaries if "Id" in s]
            if ids:
                return ids, "GetTraceSummaries, time-window match (see module docstring)"
            last_note = "no traces found in this window yet"
        except Exception as exc:  # noqa: BLE001
            last_note = f"GetTraceSummaries failed: {exc}"
            break  # a real error (e.g. no credentials) won't fix itself by polling

        if time.time() >= deadline:
            break
        time.sleep(poll_interval)

    return [], last_note or "no traces found"


def run_live(xray_wait: int, xray_poll_interval: int) -> list[dict]:
    """Send each scenario through the deployed runtime via invoke_agent()
    (pre-written, unmodified), then look up its X-Ray trace by time window."""
    import config
    import agent_orchestrator

    region = config.AWS_REGION
    report = []
    call_windows = []

    for scenario in SCENARIOS:
        session_id = f"s-live-{uuid.uuid4().hex[:8]}"
        start_ts = time.time()
        entry = {
            "id": scenario["id"],
            "session_id": session_id,
            "customer_id": scenario["customer_id"],
            "message": scenario["message"],
            "expected_routing": scenario["expected_routing"],
            "caveat": _LIVE_CAVEAT,
        }
        try:
            response = agent_orchestrator.invoke_agent(
                session_id, scenario["customer_id"], scenario["message"])
            entry["response"] = response
            entry["error"] = None
        except Exception as exc:  # noqa: BLE001 - one scenario must not lose the rest
            entry["response"] = ""
            entry["error"] = str(exc)
        entry["_start_ts"] = start_ts
        entry["_end_ts"] = time.time()
        report.append(entry)
        print(f"  [{scenario['id']}] session={session_id} -> "
              f"{'ERROR: ' + entry['error'] if entry['error'] else entry['response'][:120]}")

    print(f"\nWaiting up to {xray_wait}s per scenario for X-Ray to ingest the traces...")
    for entry in report:
        ids, note = _lookup_xray_trace_ids(
            entry["_start_ts"], entry["_end_ts"], region, xray_wait, xray_poll_interval
        )
        entry["trace_ids"] = ids
        entry["trace_note"] = note
        entry["trace_id"] = ids[0] if len(ids) == 1 else None
        label = ids[0] if len(ids) == 1 else (f"{len(ids)} candidates: {ids}" if ids else "none")
        print(f"  [{entry['id']}] X-Ray trace(s): {label}  ({note})")
        del entry["_start_ts"], entry["_end_ts"]

    return report


# ─────────────────────────────────────────────────────────────
# EVIDENCE OUTPUT
# ─────────────────────────────────────────────────────────────

def _write_evidence(mode: str, report: list[dict], out_dir: pathlib.Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    for entry in report:
        transcript = out_dir / f"{entry['id']}.txt"
        lines = [
            f"scenario:         {entry['id']}",
            f"session_id:       {entry['session_id']}",
            f"customer_id:      {entry['customer_id']}",
            f"message:          {entry['message']}",
            f"expected_routing: {entry['expected_routing']}",
        ]
        if mode == "offline":
            lines += [
                f"actual_agents_called: {entry['actual_agents_called']}",
                f"tool_calls:           {entry['tool_calls']}",
            ]
        else:
            if entry.get("trace_ids"):
                lines.append(f"xray_trace_ids:   {', '.join(entry['trace_ids'])}")
            else:
                lines.append("xray_trace_ids:   (none found)")
            lines.append(f"xray_lookup_note: {entry['trace_note']}")
        if entry.get("error"):
            lines.append(f"error:            {entry['error']}")
        lines += ["", "response:", entry.get("response", "") or "(empty)",
                  "", "caveat:", entry["caveat"], ""]
        transcript.write_text("\n".join(lines), encoding="utf-8")

    index_lines = [
        f"# Scenario transcripts -- {mode.upper()}",
        "",
    ]
    if mode == "offline":
        index_lines += [
            "Run in-process against the real five-agent graph and "
            "`harness/scripted_model.py` (a rule-based stand-in for the "
            "model, not a live LLM decision) - no deployed runtime, no "
            "X-Ray trace. This shows the tool wiring is correct for these "
            "three prompts, not that a real model would route them the "
            "same way.",
            "",
            "| scenario | message | expected routing | agents actually called |",
            "|---|---|---|---|",
        ]
        for e in report:
            index_lines.append(
                f"| {e['id']} | {e['message']} | {e['expected_routing']} | "
                f"{e['actual_agents_called']} |"
            )
    else:
        index_lines += [
            "Run against the real deployed AgentCore Runtime via "
            "`invoke_agent()`. The X-Ray trace id is a time-window lookup "
            "(see run_scenarios.py's module docstring) - if more than one "
            "candidate trace appears, all are listed rather than guessed.",
            "",
            "| scenario | message | expected routing | X-Ray trace id(s) |",
            "|---|---|---|---|",
        ]
        for e in report:
            trace_col = ", ".join(e.get("trace_ids") or []) or "(none found)"
            index_lines.append(
                f"| {e['id']} | {e['message']} | {e['expected_routing']} | {trace_col} |"
            )

    index_lines += ["", f"Per-scenario transcripts: `{out_dir.name}/<scenario-id>.txt`", ""]
    (out_dir / "INDEX.md").write_text("\n".join(index_lines), encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--offline", action="store_true",
                             help="Run the real agent graph in-process, no AWS.")
    mode_group.add_argument("--live", action="store_true",
                             help="Run against a real deployed AgentCore Runtime.")
    parser.add_argument("--run-name", default=None,
                         help="Evidence subdirectory name under evidence/. "
                              "Defaults to 'live' or 'offline' matching the mode.")
    parser.add_argument("--out", default=None,
                         help="Override the full output directory "
                              "(default: evidence/<run-name>/scenarios).")
    parser.add_argument("--xray-wait", type=int, default=90,
                         help="Seconds to poll X-Ray per scenario before giving up "
                              "(--live only). Traces take 30-60s to appear.")
    parser.add_argument("--xray-poll-interval", type=int, default=10)
    args = parser.parse_args(argv)

    mode = "live" if args.live else "offline"
    run_name = args.run_name or mode
    out_dir = pathlib.Path(args.out) if args.out else ROOT / "evidence" / run_name / "scenarios"

    print(f"Scenario runner -- {mode} mode")
    print(f"{len(SCENARIOS)} scenarios, writing evidence to {out_dir}\n")

    if mode == "offline":
        report = run_offline()
    else:
        report = run_live(args.xray_wait, args.xray_poll_interval)

    _write_evidence(mode, report, out_dir)

    failures = sum(1 for e in report if e.get("error"))
    print(f"\nWrote {out_dir / 'INDEX.md'}")
    if failures:
        print(f"{failures} of {len(report)} scenarios errored - see the transcripts.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
