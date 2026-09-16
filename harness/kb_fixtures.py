"""Passages standing in for the three synced Knowledge Bases.

Term-overlap retrieval, not Titan embeddings. Enough to prove the fan-out
collects three distinct result sets; not enough to claim retrieval quality.
"""

_DOMAINS = {
    "returns": [
        ("Standard customers may return any item within 30 days of delivery "
         "for a full refund. Premium customers have 60 days.",
         "s3://policy-docs/policies/returns/returns-policy.md"),
        ("Items must be unused and in original packaging to qualify for a "
         "full refund. Opened electronics carry a 15% restocking fee.",
         "s3://policy-docs/policies/returns/returns-conditions.md"),
    ],
    "shipping": [
        ("Standard shipping is free on orders over $50 and arrives in 5-7 "
         "business days. Premium members receive free two-day shipping.",
         "s3://policy-docs/policies/shipping/shipping-policy.md"),
    ],
    "warranty": [
        ("All electronics carry a 12-month limited warranty covering "
         "manufacturing defects. Premium members receive 24 months.",
         "s3://policy-docs/policies/warranty/warranty-policy.md"),
    ],
}

_BY_KB = {
    "KBRETURNS01":  "returns",
    "KBSHIPPING1":  "shipping",
    "KBWARRANTY1":  "warranty",
}


def passages(kb_id: str, query: str = "", top_k: int = 3) -> list[dict]:
    """Return fixture passages for a KB id, shaped like the real retrieve()."""
    domain = _BY_KB.get(kb_id)
    if domain is None:
        raise KeyError(f"No fixture for KB id {kb_id!r}")
    rows = _DOMAINS[domain][:top_k]
    return [{"text": t, "source": s, "score": 0.9 - i * 0.1}
            for i, (t, s) in enumerate(rows)]
