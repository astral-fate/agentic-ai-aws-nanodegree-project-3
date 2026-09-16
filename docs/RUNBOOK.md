# Runbook

Two paths. **Offline** (this machine, any machine): proves the wiring, no
AWS account needed, runs in seconds. **Live** (AWS CloudShell): the actual
graded run, costs money while resources are up, and has not been executed
yet from this repository — no AWS credentials are available on this
machine and the Udacity Cloud Lab had not been launched at the time of
this commit.

## Offline: test the wiring

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests_offline/ -v              # 61 passed in 18.07s
python scripts/run_scenarios.py --offline       # 3 scenario transcripts
python scripts/run_adversarial.py --offline     # 6 guardrail-config transcripts
```

**If a test fails:** re-read `docs/TESTING.md`'s "proven offline" list
first — it names the exact test and what it checks. Most failures trace to
either `src/agent_orchestrator.py` diverging from a starter-preserved
region (compare against `STARTER_PROVENANCE.md`) or `harness/*` being out
of sync with a tool's signature (check `docs/superpowers/plans/…`'s
cross-task interface table).

## Live: deploy, test, screenshot, package, teardown

Everything below runs from **AWS CloudShell**, not a laptop — CloudShell
already has the AWS CLI, credentials and Python. See
`cloudshell/README.md` for the full phase-by-phase description; this is
the operational sequence.

### 1. Deploy

```bash
bash cloudshell/deploy-e2e-v02.sh
```

Paste the whole file into a CloudShell terminal (or upload it via Actions →
Upload file, then run it). It is self-contained — every project file it
needs is embedded as a quoted heredoc, so no `git clone` is required. It
materializes the project under `~/novamart-project`, creates a venv at
`~/.novamart-venv` (CloudShell's own `python3` does not have
`strands-agents` or `python-dotenv`), runs a permission preflight that
reports every missing IAM permission at once before the first write, then
deploys CloudFormation (`infrastructure/starter_stack.yaml`), seeds data,
provisions the S3 Vectors bucket and its three indexes plus the three
Knowledge Bases (**not** created by CloudFormation — see
`docs/TESTING.md`'s corrections), and deploys the agent itself via
`src/agent_orchestrator.py deploy`.

**If deploy fails partway:** the script is resumable — state lives in
`~/.novamart-state/`, and re-running skips anything that already exists.
Every AWS-mutating call has a console fallback printed with the real
resource names filled in, so a single failed step does not require
restarting from zero.

### 2. Test

```bash
bash cloudshell/deploy-e2e-v02.sh --test-only
```

Runs `tests/test_agent.py all` (through the venv) and tees output to
`evidence/live/pytest_output.txt`. The grader's own exit code is always 0,
so read the printed `Score: X/Y pts` line, not the exit code. Then:

```bash
bash cloudshell/deploy-e2e-v02.sh   # (scenario + adversarial phases run as
                                      #  part of a full deploy, not --test-only)
```

produces `scripts/run_scenarios.py --live` and
`scripts/run_adversarial.py --live` transcripts under `evidence/live/`.

**If the score is below 120/120:** the failure detail printed by
`tests/test_agent.py` names the exact check (e.g. `GUARDRAIL_ID not set`,
`Runtime status is CREATING`). Most partial scores trace to a resource
that has not finished provisioning yet (Knowledge Base ingestion, runtime
readiness) rather than a code defect — re-run `--test-only` after a short
wait before changing anything.

### 3. Screenshot

Run **locally**, not from CloudShell (CloudShell has no GUI to drive a
browser from):

```bash
python scripts/capture_console.py
```

Requires a signed-in AWS console session in Chrome. Produces six targets;
the two the rubric requires are `01-test-score.png` (a terminal-styled
render of a real `tests/test_agent.py all` subprocess run) and
`02-xray-service-map.png` (CloudWatch → X-Ray → Service map). Run
`scripts/run_scenarios.py --live` first so there is a trace for the map to
show — traces take 30-60s to appear, so the capture sleeps and retries.

**If the Service Map screenshot comes back empty:** the capture does not
save a blank canvas as if it were evidence — if it still looks empty after
every retry, the file is not written at all. Re-run
`scripts/run_scenarios.py --live` to generate fresh traffic, wait longer,
and retry the capture. If the map still shows nothing, check that `test`
or `chat` mode (not `demo` — see the known limitation below) is what
generated the traffic; `demo` publishes no trace at all.

Copy or upload the resulting `screenshots/` directory into
`~/novamart-project/evidence/live/screenshots/` inside the CloudShell
session before packaging.

### 4. Package

```bash
bash cloudshell/deploy-e2e-v02.sh --package
```

Builds `~/novamart-submission.zip`: `src/agent_orchestrator.py` (plus the
rest of `src/`, `tests/`, `infrastructure/`), a `.env` with every value
redacted to `REDACTED`, staged screenshots, adversarial and scenario
transcripts, and its own `INDEX.md` stating plainly which of the two
required screenshots were actually found. Download it via CloudShell's
Actions → Download file, using the absolute path the script prints.

**If a required screenshot is missing:** `--package`'s own `INDEX.md` says
so explicitly (a `MISSING` row) rather than silently omitting it — go back
to step 3.

### 5. Teardown

```bash
bash cloudshell/deploy-e2e-v02.sh --teardown
```

Delegates to `infrastructure/cleanup.py --yes`, dry-run by default when
run directly. Deletion order matters and is deliberate: **the resources
that bill while idle go first**, so an interrupted cleanup still stops the
meter. Knowledge Bases and their data sources, the S3 Vectors bucket and
indexes, the AgentCore runtime and memory, the guardrail, the policy
bucket's objects, and finally the CloudFormation stack — then the script's
own local state (`~/.novamart-state`, `~/novamart-project`).

**If teardown fails partway:** re-run it — `cleanup.py` is designed to be
safe to re-invoke, and it reports what it could not find rather than
erroring on an already-deleted resource. If `cleanup.py` itself cannot
import `config` because credentials are missing entirely, it prints that
plainly and exits 0 with "nothing to discover" rather than a fake success
— but any other failure (a real bug, a malformed export) propagates as an
uncaught exception on purpose, because silently reporting "nothing found"
when a resource is actually still billing is the worse failure mode.

### Cost warning

| Resource | Billing behavior |
|---|---|
| DynamoDB, S3 objects | effectively free at this project's volume |
| Bedrock model invocations | per-token, scales with how much you test |
| **Bedrock Knowledge Base storage + its S3 Vectors index** | **bills while idle, whether or not anything queries it** |

The last row is the one that empties a lab budget. Screenshot what you
need, then tear down immediately — do not leave the stack up "just in
case."

## Known limitation: `demo` does not trace

`src/demo.py` is a do-not-modify starter file. It imports the builder
functions and `trace` (the local terminal `AgentTrace`, unrelated to
X-Ray) from `agent_orchestrator` and calls `orchestrator(prompt)` with no
`tracer.trace_request(...)` wrapper anywhere. Running `python src/demo.py`
therefore publishes no X-Ray trace at all. Tracing is wired into `test`
mode, `chat` mode, and the deployed runtime path (`serve`, via
`_serve_http`) — use one of those three to generate the traffic behind the
Service Map screenshot, not `demo`.
