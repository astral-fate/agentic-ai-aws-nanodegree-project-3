import sys
sys.path.insert(0, "scripts")


def test_every_required_class_is_covered():
    import run_adversarial
    kinds = {c["kind"] for c in run_adversarial.CASES}
    assert kinds == {"prompt-injection", "competitor", "legal-threat",
                     "pii-block", "pii-anonymize", "profanity"}


def test_offline_mode_asserts_payload_not_enforcement():
    """Offline we can only check the guardrail config would catch it.

    The stub does not enforce anything, so the offline report must say so
    rather than printing a pass that reads like a live block.
    """
    import run_adversarial
    report = run_adversarial.run_offline()
    assert all(r["claim"] == "config-covers-case" for r in report)
    assert all("not enforcement" in r["caveat"] for r in report)


def test_live_index_surfaces_the_error_reason_not_just_the_verdict(tmp_path):
    """A live run where every case errors (e.g. the model call itself
    failed) must not leave the exception text buried in per-case .txt files
    only - the INDEX.md summary table needs it too, so a future run can
    diagnose itself without opening seven files."""
    import run_adversarial

    report = [{
        "kind": "profanity", "prompt": "x", "expect": "blocked",
        "claim": "live-runtime-response", "caveat": "c",
        "response": "", "verdict": "error",
        "error": "AccessDeniedException: model access not granted",
    }]
    run_adversarial._write_evidence("live", report, tmp_path)
    index = (tmp_path / "INDEX.md").read_text(encoding="utf-8")
    assert "AccessDeniedException" in index
    assert "error" in index.lower()
