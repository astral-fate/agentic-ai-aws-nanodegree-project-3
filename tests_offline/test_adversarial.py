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
