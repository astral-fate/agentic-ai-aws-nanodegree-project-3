from harness import fakes


def test_guardrail_request_has_every_required_policy(orchestrator):
    fakes.recorded.clear()
    gid, version = orchestrator.create_guardrail()

    req = fakes.recorded["create_guardrail"][-1]

    filters = {f["type"]: f for f in req["contentPolicyConfig"]["filtersConfig"]}
    for kind in ("SEXUAL", "VIOLENCE", "HATE"):
        assert filters[kind]["inputStrength"] == "HIGH"
        assert filters[kind]["outputStrength"] == "HIGH"
    for kind in ("INSULTS", "MISCONDUCT"):
        assert filters[kind]["inputStrength"] == "MEDIUM"

    pii = {e["type"]: e["action"] for e in
           req["sensitiveInformationPolicyConfig"]["piiEntitiesConfig"]}
    assert pii["CREDIT_DEBIT_CARD_NUMBER"] == "BLOCK"
    assert pii["US_SOCIAL_SECURITY_NUMBER"] == "BLOCK"
    assert pii["EMAIL"] == "ANONYMIZE"
    assert pii["PHONE"] == "ANONYMIZE"

    topics = {t["name"].lower(): t for t in req["topicPolicyConfig"]["topicsConfig"]}
    assert len(topics) == 3
    assert all(t["type"] == "DENY" for t in topics.values())

    words = req["wordPolicyConfig"]["managedWordListsConfig"]
    assert any(w["type"] == "PROFANITY" for w in words)

    assert req["blockedInputMessaging"] and req["blockedOutputsMessaging"]


def test_guardrail_is_versioned_not_draft(orchestrator):
    fakes.recorded.clear()
    gid, version = orchestrator.create_guardrail()
    assert fakes.recorded.get("create_guardrail_version"), \
        "create_guardrail_version() was never called — GUARDRAIL_VERSION would be DRAFT"
    assert version != "DRAFT"
