import hashlib, pathlib, re

PROVENANCE = pathlib.Path("STARTER_PROVENANCE.md")

def _recorded_hashes():
    text = PROVENANCE.read_text(encoding="utf-8")
    return dict(re.findall(r"\|\s*`([^`]+)`\s*\|\s*`([0-9a-f]+)…?`", text))

def test_starter_files_match_recorded_hashes():
    """Starter files must not drift. If this fails, either a starter file was
    edited (not allowed) or STARTER_PROVENANCE.md was not updated."""
    recorded = _recorded_hashes()
    assert recorded, "STARTER_PROVENANCE.md records no hashes"
    for name, prefix in recorded.items():
        actual = hashlib.sha256(pathlib.Path(name).read_bytes()).hexdigest()
        assert actual.startswith(prefix), f"{name} changed since provenance was recorded"

def test_orchestrator_is_not_listed_as_starter():
    """agent_orchestrator.py is ours. It must never appear in the provenance table."""
    assert "agent_orchestrator.py" not in _recorded_hashes()
