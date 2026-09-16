# Console + test-run screenshots

Captured by `scripts/capture_console.py`. `01-test-score.png` is a real
subprocess run of `tests/test_agent.py all` rendered as a terminal image;
the rest are real screenshots of a real, signed-in Chrome session against
the AWS console. Anything that did not paint (or, for the Service Map,
still looked empty after the ingestion-delay retries) is reported as
BLANK/NOT SAVED below rather than listed as evidence.

| File | Shows | Source |
|---|---|---|
| `07-kb-returns-detail.png` | Bedrock -> Knowledge Bases -> novamart-returns-policy-kb: Titan Embed Text v2, S3 Vectors backing store, returns-policy-index, and the data source's sync status. | `https://us-east-1.console.aws.amazon.com/bedrock/home?region=us-east-1#/knowledge-bases/knowledge-base/61JQLUIHYY` |
| `07-kb-shipping-detail.png` | Bedrock -> Knowledge Bases -> novamart-shipping-policy-kb: Titan Embed Text v2, S3 Vectors backing store, shipping-policy-index, and the data source's sync status. | `https://us-east-1.console.aws.amazon.com/bedrock/home?region=us-east-1#/knowledge-bases/knowledge-base/AL8HEH5D7V` |
| `07-kb-warranty-detail.png` | Bedrock -> Knowledge Bases -> novamart-warranty-policy-kb: Titan Embed Text v2, S3 Vectors backing store, warranty-policy-index, and the data source's sync status. | `https://us-east-1.console.aws.amazon.com/bedrock/home?region=us-east-1#/knowledge-bases/knowledge-base/HRQX4Y3ENP` |
| `03-knowledge-bases.png` | Bedrock -> Knowledge Bases: all three policy KBs, each with a synced data source (novamart-returns/shipping/warranty-policy-kb). | `https://us-east-1.console.aws.amazon.com/bedrock/home?region=us-east-1#/knowledge-bases` |
| `05-guardrail.png` | Bedrock -> Guardrails -> udacity-agentcore-guardrail, showing a numbered version. .env records version 1. | `https://us-east-1.console.aws.amazon.com/bedrock/home?region=us-east-1#/guardrails` |
