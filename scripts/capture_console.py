#!/usr/bin/env python3
"""
Screenshot the AWS console pages (and the local test run) that evidence this
project's live deployment.

    python scripts/capture_console.py --out evidence/live/screenshots

Adapted from ../agentic-ai-aws-nanodegree-project-2/scripts/capture_console.py,
which drove a real Chrome against a logged-in console profile and captured six
live console screenshots for that project. Kept verbatim: the sign-in flow
(federated + interactive-profile fallback), `dismiss_overlays`, `is_signin`,
and the `settle()` poll-until-painted loop with its blank-render floor
(SHELL_TEXT_CHARS). Rewritten for this project: the target list (six shots
below instead of project 2's nine), the X-Ray Service Map capture (which did
not exist in project 2 - it needed the 30-60s trace-ingestion wait and a
reject-rather-than-save blank check), and the terminal-style capture of the
grader's own output (project 2 had no equivalent - all nine of its shots were
plain console pages).

These are **real** captures. `01-test-score.png` is a screenshot of a
`<pre>` block holding the literal, unedited stdout of a real subprocess run of
`tests/test_agent.py all` - never hand-typed or fabricated text dressed up to
look like a terminal. The other five are real console screenshots, taken by
driving a real Chrome session. A page that does not paint (or, for the X-Ray
shot, a service map with no data yet) is reported as BLANK/NOT SAVED rather
than filed as evidence - a screenshot that only looks plausible is worse than
an obvious failure, because a reviewer trusts it on sight.

Signing in
----------
Same as project 2's script:

* **Federated sign-in (default when credentials are available).**
  ``sts:GetFederationToken`` turns an IAM user's keys into a URL that logs a
  browser in with no typing, so the run is headless and unattended. The
  account root cannot call this API, so it needs an IAM user; put its keys in
  ``.env`` as ``EVIDENCE_AWS_ACCESS_KEY_ID`` / ``EVIDENCE_AWS_SECRET_ACCESS_KEY``.

* **Persistent profile.** With no evidence credentials, the first run opens a
  visible Chrome window and waits for you to sign in. The session is saved to
  ``.aws-console-profile/`` (git-ignored), so later runs are automatic.

Why Playwright rather than the browser extension: Playwright drives its own
Chrome, so it needs no per-site permission grant and no already-signed-in
profile.

This machine has no AWS credentials and nothing deployed (see MEMORY.md), so
none of this has been executed here - only read and reasoned about. Treat
every capture as unverified until a real run produces the six PNGs.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - dependency guidance
    print(
        "playwright is not installed. Run:\n"
        "  pip install playwright\n"
        "  python -m playwright install chromium",
        file=sys.stderr,
    )
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parent.parent
SIGNIN_HOSTS = ("signin.aws.amazon.com", "signin.aws.com")

# The console shell - nav bar, search, footer - renders immediately and
# contributes roughly this much text. At or below it, the page body has not
# painted: a screenshot taken then is a blank frame under a correct-looking
# header, which is worse than an obvious failure because it looks plausible.
# (Same floor and same reasoning as project 2's capture_console.py.)
SHELL_TEXT_CHARS = 1200

# Phrases the CloudWatch/X-Ray console itself prints when a time window has
# no data. Text-length alone does not catch this case for the Service Map:
# the console shell, filters and legend still render plenty of text even
# when the graph canvas is empty, so this list is the second half of the
# blank-render check for that one shot.
_NO_DATA_PHRASES = (
    "no data available",
    "no traces found",
    "no trace data",
    "there is no data for the selected time range",
    "no service map data",
    "no data to display",
    "no data was found",
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def console(region: str, path: str) -> str:
    return f"https://{region}.console.aws.amazon.com/{path}"


# ── Federated sign-in (unchanged from project 2) ─────────────────────────────

def mint_signin_url(region: str, duration: int = 3600) -> str | None:
    """
    Turn EVIDENCE_AWS_* credentials into a console sign-in URL.

    Returns None when no evidence credentials are configured, so the caller
    can fall back to an interactive sign-in.
    """
    access = os.environ.get("EVIDENCE_AWS_ACCESS_KEY_ID", "").strip()
    secret = os.environ.get("EVIDENCE_AWS_SECRET_ACCESS_KEY", "").strip()
    if not access or not secret:
        return None

    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        print("boto3 is needed for federated sign-in", file=sys.stderr)
        return None

    sts = boto3.client(
        "sts",
        region_name=region,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        aws_session_token=None,
    )

    # A federation session's effective permissions are the *intersection* of
    # this policy and the IAM user's own, so "*" here does not grant write
    # access - it declines to narrow further than whatever that user already
    # has (intended to be a read-only evidence-capture user).
    policy = json.dumps({
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Action": "*", "Resource": "*"}],
    })

    try:
        creds = sts.get_federation_token(
            Name="evidence-capture", Policy=policy, DurationSeconds=duration
        )["Credentials"]
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if "AccessDenied" in code or "not authorized" in str(exc):
            print(
                "GetFederationToken was denied.\n"
                "  The account root cannot call it at all, and an IAM user needs\n"
                "  an explicit sts:GetFederationToken grant.",
                file=sys.stderr,
            )
        else:
            print(f"GetFederationToken failed: {exc}", file=sys.stderr)
        return None

    session = urllib.parse.quote(json.dumps({
        "sessionId": creds["AccessKeyId"],
        "sessionKey": creds["SecretAccessKey"],
        "sessionToken": creds["SessionToken"],
    }))
    with urllib.request.urlopen(
        f"https://signin.aws.amazon.com/federation?Action=getSigninToken&Session={session}",
        timeout=30,
    ) as resp:
        token = json.load(resp)["SigninToken"]

    destination = urllib.parse.quote(
        f"https://{region}.console.aws.amazon.com/console/home?region={region}"
    )
    return ("https://signin.aws.amazon.com/federation?Action=login"
            f"&Issuer=evidence-capture&Destination={destination}&SigninToken={token}")


def load_dotenv(path: Path) -> None:
    """Load .env into the environment without overwriting what is already set."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


# ── Page helpers (unchanged from project 2) ──────────────────────────────────

def dismiss_overlays(page) -> None:
    """
    Close the console's onboarding popover.

    The "Service menu" tooltip renders on top of the content pane and lands in
    every screenshot taken soon after sign-in. Escape closes it; the explicit
    close buttons are tried too because the markup varies by page.
    """
    try:
        page.keyboard.press("Escape")
        for selector in ('button[aria-label="Close"]',
                         'button[data-testid="close-button"]',
                         '[class*="awsui_dismiss"] button'):
            for handle in page.query_selector_all(selector)[:3]:
                try:
                    handle.click(timeout=1200)
                except Exception:  # noqa: BLE001 - best effort only
                    pass
        page.wait_for_timeout(600)
    except Exception:  # noqa: BLE001 - never block a capture on this
        pass


def is_signin(page) -> bool:
    return any(host in page.url for host in SIGNIN_HOSTS)


def settle(page, initial_wait: int, attempts: int = 4, expect: str = "") -> int:
    """
    Wait for the console SPA to actually paint.

    These are single-page apps behind a fragment router: DOMContentLoaded
    fires long before any content exists, so poll the rendered text rather
    than trusting a fixed sleep.
    """
    page.wait_for_timeout(initial_wait)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except PWTimeout:
        pass  # some console pages poll forever and never go idle

    def ready() -> tuple[int, bool]:
        try:
            text = page.evaluate("() => document.body?.innerText || ''")
        except Exception:  # noqa: BLE001 - page may be mid-navigation
            return 0, False
        enough = len(text) > SHELL_TEXT_CHARS
        if expect:
            enough = enough and expect in text
        return len(text), enough

    length, done = ready()
    for _ in range(attempts):
        if done:
            return length
        page.wait_for_timeout(5000)
        length, done = ready()
    return length if done else min(length, SHELL_TEXT_CHARS)


def _try_set_last_5_minutes(page) -> None:
    """Best-effort click on a '5 minutes' time-range control.

    The exact control varies by console release and was never seen live from
    this machine, so this only ever helps - it never blocks a capture if the
    selector guesses are wrong.
    """
    for text in ("Last 5 minutes", "5m", "5 minutes"):
        try:
            locator = page.get_by_text(text, exact=False)
            if locator.count() > 0:
                locator.first.click(timeout=1500)
                page.wait_for_timeout(800)
                return
        except Exception:  # noqa: BLE001
            continue


# ── 01: the grader's own output, screenshotted as a terminal ────────────────

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _terminal_html(title: str, body_text: str) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #0b0f14; }}
  .bar {{ background: #1b2129; color: #8b98a5; font: 12px -apple-system, sans-serif;
          padding: 8px 16px; border-bottom: 1px solid #2a323c; }}
  .term {{ background: #0b0f14; color: #d7dee3;
           font-family: 'Cascadia Code', Consolas, Menlo, monospace;
           font-size: 14px; line-height: 1.45; padding: 20px 24px;
           white-space: pre-wrap; word-break: break-word; margin: 0; }}
</style></head>
<body>
  <div class="bar">{html.escape(title)}</div>
  <pre class="term">{html.escape(body_text)}</pre>
</body></html>"""


def capture_test_score(ctx, out_dir: Path, timeout: int) -> dict:
    """
    Run `tests/test_agent.py all` for real, render its real stdout/stderr as
    a terminal-styled page, and screenshot that page.

    Nothing here fabricates the score: whatever the subprocess actually
    prints is what appears in the image, whether that is 120/120 or not.
    """
    name = "01-test-score"
    print(f"\n  {name}")
    cmd = [sys.executable, "tests/test_agent.py", "all"]
    try:
        proc = subprocess.run(
            cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as exc:
        print(f"    FAILED: could not run tests/test_agent.py: {exc}")
        return {"status": "failed", "reason": str(exc)}
    except subprocess.TimeoutExpired:
        print(f"    FAILED: did not finish within {timeout}s")
        return {"status": "failed", "reason": "timeout"}

    raw = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    text = _strip_ansi(raw)
    match = re.search(r"Score:\s*(\d+)/(\d+)\s*pts", text)
    score_line = match.group(0) if match else None
    clean_pass = bool(match and match.group(1) == match.group(2))

    # Keep the tail: a long run's most important line (the score) is at the
    # end, and a full_page screenshot of an enormous <pre> is unusable.
    doc = _terminal_html("tests/test_agent.py all", text[-14000:])
    tmp_path = out_dir / "_terminal_test_score.html"
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_path.write_text(doc, encoding="utf-8")

    page = ctx.new_page()
    out_path = out_dir / f"{name}.png"
    try:
        page.set_viewport_size({"width": 1200, "height": 1400})
        page.goto(tmp_path.resolve().as_uri())
        page.wait_for_timeout(400)
        page.screenshot(path=str(out_path), full_page=True)
    finally:
        page.close()
        try:
            tmp_path.unlink()
        except OSError:
            pass

    if score_line:
        suffix = "" if clean_pass else "  -- NOT a clean pass, check the transcript"
        print(f"    saved {out_path.name} -- {score_line}{suffix}")
    else:
        print(f"    saved {out_path.name} -- no 'Score: X/Y pts' line found in the output")
    return {
        "status": "captured", "file": out_path.name,
        "score_line": score_line, "clean_pass": clean_pass,
        "note": "Real stdout of `tests/test_agent.py all`, rendered as a terminal.",
    }


# ── 02: X-Ray Service Map - must wait for ingestion, must not save blank ────

def capture_xray_service_map(page, out_dir: Path, region: str,
                              wait_before: int, retries: int, retry_wait: int) -> dict:
    """
    Traces take 30-60s to reach X-Ray after a request (see run_scenarios.py,
    which should be run first). This sleeps out that delay, then retries
    the capture rather than ever saving a blank canvas: a Service Map with no
    data still renders a full console shell (nav, filters, legend), so the
    generic text-length blank check alone would happily save that. This adds
    a second check against the console's own "no data" messaging, and if
    every attempt still looks blank, the file is **not written at all**.
    """
    name = "02-xray-service-map"
    print(f"\n  {name}")
    print(f"    waiting {wait_before}s for X-Ray to ingest the scenario traces...")
    time.sleep(wait_before)

    urls = [
        console(region, f"cloudwatch/home?region={region}#xray:service-map"),
        console(region, f"xray/home?region={region}#/service-map"),
    ]

    for attempt in range(1, retries + 1):
        for url in urls:
            try:
                page.goto(url, wait_until="commit", timeout=90000)
            except Exception as exc:  # noqa: BLE001
                print(f"    attempt {attempt}: could not load {url}: {exc}")
                continue
            if is_signin(page):
                print("    bounced to the sign-in page - cannot capture without a session")
                return {"status": "failed", "reason": "signed out"}

            _try_set_last_5_minutes(page)
            length = settle(page, 9000, attempts=6)
            try:
                lowered = page.evaluate("() => (document.body?.innerText || '').toLowerCase()")
            except Exception:  # noqa: BLE001
                lowered = ""
            looks_blank = (length <= SHELL_TEXT_CHARS) or any(p in lowered for p in _NO_DATA_PHRASES)

            if not looks_blank:
                dismiss_overlays(page)
                out_path = out_dir / f"{name}.png"
                page.screenshot(path=str(out_path), full_page=True)
                print(f"    saved {out_path.name} ({length} chars) on attempt {attempt}/{retries}")
                return {"status": "captured", "file": out_path.name, "url": page.url}
            print(f"    attempt {attempt}/{retries}: still blank/empty ({length} chars) at {url}")

        if attempt < retries:
            print(f"    sleeping {retry_wait}s before retrying...")
            time.sleep(retry_wait)

    total_wait = wait_before + (retries - 1) * retry_wait
    print(f"    NOT SAVED: the Service Map still looks blank after {retries} attempts "
          f"(~{total_wait}s total wait). Re-run scripts/run_scenarios.py, wait longer, "
          "and re-run this capture with a larger --xray-wait.")
    return {"status": "blank_rejected"}


# ── 03-06: plain console pages ───────────────────────────────────────────────

def build_targets(region: str, project_name: str) -> list[dict]:
    """The four supporting console pages. Names mirror the resource-naming
    conventions in cloudshell/_deploy-e2e.template.sh and
    src/agent_orchestrator.py, so the `expect` checks look for the actual
    names this project's deploy creates rather than a guess."""
    runtime_name = f"{project_name}-runtime".replace("-", "_")
    guardrail_name = f"{project_name}-guardrail"
    guardrail_version = os.environ.get("GUARDRAIL_VERSION", "").strip()
    guardrail_id = os.environ.get("GUARDRAIL_ID", "").strip()
    log_group = f"/aws/bedrock/agentcore/{project_name}"

    guardrail_note = f"Bedrock -> Guardrails -> {guardrail_name}, showing a numbered version."
    if guardrail_version and guardrail_version != "DRAFT":
        guardrail_note += f" .env records version {guardrail_version}."
    else:
        guardrail_note += " .env still shows DRAFT/blank -- verify a numbered version by hand."

    return [
        {
            "name": "03-knowledge-bases",
            "url": console(region, f"bedrock/home?region={region}#/knowledge-bases"),
            "note": "Bedrock -> Knowledge Bases: all three policy KBs, each with a "
                    "synced data source (novamart-returns/shipping/warranty-policy-kb).",
            "wait": 10000, "attempts": 12, "expect": "novamart",
            "warm_url": console(region, f"bedrock/home?region={region}"),
        },
        {
            "name": "04-agentcore-runtime",
            "url": console(region, f"bedrock-agentcore/home?region={region}#/runtimes"),
            "alt_urls": [console(region, f"bedrock-agentcore/home?region={region}#/agents")],
            "note": f"Bedrock -> AgentCore -> Runtime: {runtime_name}, status READY.",
            "wait": 12000, "attempts": 14, "expect": runtime_name,
            "warm_url": console(region, f"bedrock-agentcore/home?region={region}#"),
        },
        {
            "name": "05-guardrail",
            "url": (console(region, f"bedrock/home?region={region}#/guardrails/{guardrail_id}")
                    if guardrail_id else console(region, f"bedrock/home?region={region}#/guardrails")),
            "alt_urls": [console(region, f"bedrock/home?region={region}#/guardrails")],
            "note": guardrail_note,
            "wait": 10000, "attempts": 12, "expect": guardrail_name,
        },
        {
            "name": "06-cloudwatch-logs",
            "url": console(
                region,
                f"cloudwatch/home?region={region}#logsV2:log-groups/log-group/"
                + urllib.parse.quote(log_group, safe="").replace("%", "$25"),
            ),
            "alt_urls": [console(region, f"cloudwatch/home?region={region}#logsV2:log-groups")],
            "note": f"CloudWatch -> Logs -> {log_group}: agent invocation entries.",
            "wait": 11000, "attempts": 12,
            "warm_url": console(region, f"cloudwatch/home?region={region}#logsV2:log-groups"),
        },
    ]


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", default="evidence/live/screenshots")
    parser.add_argument("--region", default=None,
                        help="Defaults to $AWS_REGION / .env, then us-east-1.")
    parser.add_argument("--profile", default=".aws-console-profile",
                        help="Chrome profile that keeps an interactive login.")
    parser.add_argument("--signin-url", default=None,
                        help="A pre-minted federated sign-in URL.")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--login-timeout", type=int, default=300)
    parser.add_argument("--skip-test-score", action="store_true",
                        help="Skip re-running tests/test_agent.py for 01-test-score.png.")
    parser.add_argument("--test-timeout", type=int, default=1200,
                        help="Seconds to allow tests/test_agent.py all to finish.")
    parser.add_argument("--xray-wait", type=int, default=90,
                        help="Seconds to sleep before the first Service Map attempt "
                             "(X-Ray ingestion delay). Run scripts/run_scenarios.py first.")
    parser.add_argument("--xray-retries", type=int, default=4)
    parser.add_argument("--xray-retry-wait", type=int, default=30)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    region = args.region or os.environ.get("AWS_REGION", "us-east-1")
    project_name = os.environ.get("PROJECT_NAME", "udacity-agentcore")

    signin_url = args.signin_url or mint_signin_url(region)
    headless = args.headless or bool(signin_url)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    profile = Path(args.profile)
    profile.mkdir(parents=True, exist_ok=True)

    print(f"Capturing console evidence into {out}/")
    print(f"  region  : {region}")
    print(f"  project : {project_name}")
    print(f"  sign-in : {'federated (headless)' if signin_url else 'interactive profile'}")

    captured: list[dict] = []
    blank: list[str] = []
    failed: list[str] = []

    with sync_playwright() as pw:
        try:
            ctx = pw.chromium.launch_persistent_context(
                str(profile.resolve()),
                channel="chrome",
                headless=headless,
                viewport={"width": 1600, "height": 1000},
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception as exc:  # noqa: BLE001
            print(f"\nCould not start Chrome: {exc}", file=sys.stderr)
            print("Install Google Chrome, or: python -m playwright install chromium",
                  file=sys.stderr)
            return 1

        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # ── 01: test score - no sign-in needed, do it first ─────────────────
        if args.skip_test_score:
            print("\n  01-test-score  (skipped: --skip-test-score)")
        else:
            result = capture_test_score(ctx, out, timeout=args.test_timeout)
            if result["status"] == "captured":
                captured.append({
                    "name": "01-test-score", "file": result["file"],
                    "url": "(local subprocess, not a console page)",
                    "note": result["note"],
                })
                if not result.get("clean_pass"):
                    print("    NOTE: this was not a clean pass; the Udacity rubric wants 120/120.")
            else:
                failed.append("01-test-score")

        # ── sign in ──────────────────────────────────────────────────────────
        if signin_url:
            print("\nSigning in with the federated URL...")
            page.goto(signin_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(6000)
            if is_signin(page):
                print("The federated URL did not sign in - it may have expired.",
                      file=sys.stderr)
                ctx.close()
                return 2
            print("  signed in")
        else:
            page.goto(console(region, f"console/home?region={region}"),
                      wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)
            if is_signin(page):
                if headless:
                    print("\nNot signed in and --headless was requested.\n"
                          "Run once without --headless, or configure "
                          "EVIDENCE_AWS_* in .env.", file=sys.stderr)
                    ctx.close()
                    return 2
                print("\n" + "=" * 62)
                print("  Sign in to AWS in the Chrome window that just opened.")
                print("  This happens ONCE - the session is saved to")
                print(f"  {profile}, so later runs are automatic.")
                print(f"  Waiting up to {args.login_timeout}s...")
                print("=" * 62)
                try:
                    page.wait_for_url(
                        lambda url: not any(h in url for h in SIGNIN_HOSTS),
                        timeout=args.login_timeout * 1000,
                    )
                    page.wait_for_timeout(5000)
                    print("  signed in")
                except PWTimeout:
                    print("\nTimed out waiting for sign-in.", file=sys.stderr)
                    ctx.close()
                    return 2

        # ── 02: X-Ray Service Map - wait, retry, never save blank ───────────
        xray_result = capture_xray_service_map(
            page, out, region,
            wait_before=args.xray_wait, retries=args.xray_retries,
            retry_wait=args.xray_retry_wait,
        )
        if xray_result["status"] == "captured":
            captured.append({
                "name": "02-xray-service-map", "file": xray_result["file"],
                "url": xray_result["url"],
                "note": "CloudWatch -> X-Ray traces -> Service map, Last 5 minutes: "
                        "Orchestrator -> Worker -> KnowledgeBase call chain.",
            })
        elif xray_result["status"] == "blank_rejected":
            blank.append("02-xray-service-map")
        else:
            failed.append("02-xray-service-map")

        # ── 03-06: plain console pages ───────────────────────────────────────
        for target in build_targets(region, project_name):
            name = target["name"]
            print(f"\n  {name}")
            try:
                if target.get("warm_url"):
                    page.goto(target["warm_url"], wait_until="commit", timeout=90000)
                    settle(page, 7000, attempts=5)

                length = 0
                urls = [target["url"], *target.get("alt_urls", [])]
                for index, url in enumerate(urls):
                    page.goto(url, wait_until="commit", timeout=90000)
                    if is_signin(page):
                        raise RuntimeError("bounced to the sign-in page")
                    length = settle(page, target.get("wait", 8000),
                                    attempts=target.get("attempts", 6),
                                    expect=target.get("expect", ""))
                    if length > SHELL_TEXT_CHARS:
                        break
                    if index + 1 < len(urls):
                        print(f"    blank ({length} chars) - trying the next route")

                dismiss_overlays(page)
                path = out / f"{name}.png"
                page.screenshot(path=str(path), full_page=True)
                size = path.stat().st_size

                if length <= SHELL_TEXT_CHARS:
                    print(f"    BLANK: only {length} chars rendered "
                          f"({size:,} bytes) - not usable as evidence")
                    blank.append(name)
                else:
                    print(f"    saved {path.name} ({size:,} bytes, {length} chars)")
                    captured.append({"name": name, "file": path.name,
                                     "url": page.url, "note": target["note"]})
            except Exception as exc:  # noqa: BLE001
                print(f"    FAILED: {exc}")
                failed.append(name)

        ctx.close()

    # ── index ────────────────────────────────────────────────────────────────
    if captured:
        lines = [
            "# Console + test-run screenshots",
            "",
            "Captured by `scripts/capture_console.py`. `01-test-score.png` is a real",
            "subprocess run of `tests/test_agent.py all` rendered as a terminal image;",
            "the rest are real screenshots of a real, signed-in Chrome session against",
            "the AWS console. Anything that did not paint (or, for the Service Map,",
            "still looked empty after the ingestion-delay retries) is reported as",
            "BLANK/NOT SAVED below rather than listed as evidence.",
            "",
            "| File | Shows | Source |",
            "|---|---|---|",
        ]
        for item in captured:
            lines.append(f"| `{item['file']}` | {item['note']} | `{item['url']}` |")
        (out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n  wrote {out / 'README.md'}")

    print(f"\n{len(captured)} captured, {len(blank)} blank, {len(failed)} failed")
    if blank:
        print("blank:  " + ", ".join(blank), file=sys.stderr)
    if failed:
        print("failed: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
