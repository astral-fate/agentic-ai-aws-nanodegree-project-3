# Evidence

This directory holds every artifact that backs a claim made about this
project — grader output, adversarial-suite transcripts, scenario
transcripts, and (once a live run exists) AWS console screenshots.

**Nothing in here should be read as proof of a live deployment unless it is
under `evidence/live/`.** As of this commit, `evidence/live/` does not exist:
no AWS credentials were available on the machine that wrote this project,
and the Udacity Cloud Lab had not been launched (see
`.superpowers/sdd/2026-09-16-novamart-multi-agent-support/`'s task notes and
the top-level `MEMORY.md`). Everything currently committed under
`evidence/offline/` was produced by the offline harness
(`harness/bootstrap.py` + moto + a scripted, non-LLM model stand-in) — real
runs of real, unmodified project code, but against stubbed AWS and a
rule-based routing stand-in, never against a real model or a real deployed
runtime.

## Directory layout

```
evidence/
  offline/                   committed now — harness runs, no AWS
    adversarial/              scripts/run_adversarial.py --offline
    scenarios/                scripts/run_scenarios.py --offline
  live/                       does not exist yet — produced by a real deploy
    pytest_output.txt         tests/test_agent.py all, tee'd by the CloudShell script
    adversarial/              scripts/run_adversarial.py --live
    scenarios/                scripts/run_scenarios.py --live
    screenshots/              scripts/capture_console.py (run locally, see below)
      01-test-score.png         REQUIRED  — terminal capture of the grader showing 120/120
      02-xray-service-map.png   REQUIRED  — X-Ray Service Map, Orchestrator -> Worker -> KnowledgeBase
      03-knowledge-bases.png    supporting — all three KBs, synced data sources
      04-agentcore-runtime.png  supporting — the runtime, status READY
      05-guardrail.png          supporting — the guardrail, a numbered version
      06-cloudwatch-logs.png    supporting — the agent log group, real entries
```

## `offline/` — what it does and does not prove

| Path | Produced by | Proves | Does NOT prove |
|---|---|---|---|
| `offline/adversarial/` | `scripts/run_adversarial.py --offline` | The guardrail **configuration** (`create_guardrail()`'s real request payload) covers each of six hostile-prompt categories | That any of them were actually blocked — the offline stub enforces nothing |
| `offline/scenarios/` | `scripts/run_scenarios.py --offline` | The real, unmodified five-agent graph routes each of the three Udacity-brief scenarios through the expected tool sequence (verified against the harness's own recorded tool calls, not just the script's self-report) | That a real Bedrock model would route the same way — the offline stand-in is regex/keyword routing, not an LLM decision. No X-Ray trace exists offline; the transcripts say so explicitly rather than inventing one. |

Each `INDEX.md` inside these directories carries its own caveat text
verbatim — read the caveat, not just the verdict column, before citing a row
as evidence of anything.

## `live/` — what it will contain once the Cloud Lab is launched

Produced, in order, by `cloudshell/_deploy-e2e.template.sh`'s generated
script (`cloudshell/deploy-e2e-v02.sh`) and `scripts/capture_console.py`:

1. **`pytest_output.txt`** — the real `tests/test_agent.py all` run against
   the deployed stack, tee'd to disk. The Udacity rubric wants to see
   `Score: 120/120 pts (100%)` in it.
2. **`adversarial/`** — `scripts/run_adversarial.py --live`: the same six
   prompts sent through the real deployed runtime, with the real guardrail
   attached. This is enforcement evidence, not configuration coverage.
3. **`scenarios/`** — `scripts/run_scenarios.py --live`: the three
   Udacity-brief scenarios sent through `invoke_agent()` against the real
   deployed runtime, one transcript each, with an X-Ray trace-id lookup per
   scenario. That lookup is a **time-window** match against
   `GetTraceSummaries` (see the script's module docstring for exactly why —
   `invoke_agent()` doesn't return a trace id, and the segment is built
   server-side); if more than one trace is found in a scenario's window, all
   candidates are listed rather than one being guessed.
4. **`screenshots/`** — `scripts/capture_console.py`, run **locally**, not by
   the CloudShell script (CloudShell has no GUI to drive a browser from). It
   drives a real, signed-in Chrome session and writes:
   - `01-test-score.png` — not a console page. A real subprocess run of
     `tests/test_agent.py all`, rendered as a terminal-styled image. The
     text in the image is the literal stdout of that run.
   - `02-xray-service-map.png` — CloudWatch → X-Ray traces → Service map,
     Last 5 minutes. Traces take 30–60s to reach X-Ray after a request, so
     this capture sleeps first and then retries; **if the map still looks
     empty after every retry, the file is not written at all** rather than
     saving a blank canvas as if it were evidence. Run
     `scripts/run_scenarios.py --live` first so there is something for the
     map to show.
   - `03-06` — supporting console screenshots (Knowledge Bases, AgentCore
     Runtime, Guardrail, CloudWatch Logs).

   To get these into a submission packaged from CloudShell, copy (or
   upload via CloudShell's Actions → Upload file) the `screenshots/`
   directory into `~/novamart-project/evidence/live/screenshots/` inside the
   CloudShell session before running `--package`.

## The submission zip

`bash cloudshell/deploy-e2e-v02.sh --package` builds
`novamart-submission.zip` (printed as an absolute path; download it via
CloudShell's **Actions → Download file**, pasting that exact path). It
contains `src/agent_orchestrator.py`, a redacted `.env` (every value replaced
with `REDACTED`, key names kept), whatever screenshots are staged under
`evidence/live/screenshots/`, the adversarial transcripts, the scenario
transcripts, and its own `INDEX.md` — which states, per required screenshot,
whether it was actually found, rather than assuming a "packaged
successfully" message means the submission is complete.

## Honesty checklist before submitting

- [ ] `evidence/live/pytest_output.txt` shows `Score: 120/120 pts (100%)`
- [ ] `evidence/live/screenshots/01-test-score.png` and
      `02-xray-service-map.png` both exist and were opened and visually
      checked (a file existing is not the same as it showing what its name
      claims)
- [ ] The Service Map screenshot actually shows nodes/edges for
      Orchestrator → Worker → KnowledgeBase, not an empty canvas
- [ ] `novamart-submission.zip`'s own `INDEX.md` has no `MISSING` rows for
      the two required screenshots
