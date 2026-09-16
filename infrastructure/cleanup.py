#!/usr/bin/env python3
"""Delete everything this project created.

Dry run by default. Order matters: the resources that bill while idle go
first, so an interrupted cleanup still stops the meter.

    python infrastructure/cleanup.py          # list what would be deleted
    python infrastructure/cleanup.py --yes    # delete it

No-credentials behavior: config.py reads CloudFormation exports and calls
sts.get_caller_identity() at import time, so `import config` itself raises
when this machine has no AWS credentials (or the stack was never deployed).
Rather than let that exception crash the script, we catch it once here and
degrade to "nothing to discover" - the dry-run banner and the --yes hint
still print, and the script still exits 0, so a student who has not
deployed anything yet gets a clear message instead of a traceback.
"""
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import boto3

try:
    import config
    _CONFIG_ERROR = None
except Exception as exc:  # no credentials, no stack, no network reachability
    config = None
    _CONFIG_ERROR = exc

# Idle-billing resources first. Knowledge Bases and their S3 Vectors indexes
# cost money doing nothing; a CloudFormation stack does not.
_ORDER = [
    ("knowledge-base",       "Bedrock Knowledge Bases and their data sources"),
    ("s3-vectors",           "S3 Vectors indexes and the vector bucket"),
    ("agentcore-runtime",    "AgentCore Runtime"),
    ("agentcore-memory",     "AgentCore Memory"),
    ("guardrail",            "Bedrock Guardrail"),
    ("s3-objects",           "Objects in the policy-docs bucket"),
    ("cloudformation-stack", "The udacity-agentcore stack"),
]


def _owned(name: str) -> bool:
    """Only ever touch resources this project named."""
    if config is None:
        return False
    return bool(name) and name.startswith(config.PROJECT_NAME)


def plan() -> list[dict]:
    """Return the ordered deletion plan. Read-only - discovers, deletes nothing."""
    if config is None:
        return []
    steps = []
    for kind, why in _ORDER:
        for name in _discover(kind):
            if _owned(name) or kind == "cloudformation-stack":
                steps.append({"kind": kind, "name": name, "why": why})
    return steps


def _discover(kind: str) -> list[str]:
    """List the existing resources of one kind. Returns [] when the API is unavailable."""
    try:
        if kind == "knowledge-base":
            kbs = boto3.client("bedrock-agent", region_name=config.AWS_REGION) \
                .list_knowledge_bases().get("knowledgeBaseSummaries", [])
            return [k["name"] for k in kbs if "novamart" in k["name"].lower()]
        if kind == "guardrail":
            grs = boto3.client("bedrock", region_name=config.AWS_REGION) \
                .list_guardrails().get("guardrails", [])
            return [g["name"] for g in grs]
        if kind == "cloudformation-stack":
            return [config.PROJECT_NAME]
        if kind == "s3-objects":
            return [config.POLICY_BUCKET]
        if kind == "s3-vectors":
            return [config.VECTOR_STORE_BUCKET]
        if kind == "agentcore-runtime":
            rts = boto3.client("bedrock-agentcore-control",
                               region_name=config.AWS_REGION) \
                .list_agent_runtimes().get("agentRuntimes", [])
            return [r["agentRuntimeName"] for r in rts]
        if kind == "agentcore-memory":
            mems = boto3.client("bedrock-agentcore-control",
                                region_name=config.AWS_REGION) \
                .list_memories().get("memories", [])
            return [m["name"] for m in mems]
    except Exception as exc:
        print(f"  ! could not list {kind}: {exc}")
    return []


def _guard_account(force: bool) -> None:
    """Refuse to delete in an account that does not own the stack."""
    account = boto3.client("sts", region_name=config.AWS_REGION) \
        .get_caller_identity()["Account"]
    try:
        cfn = boto3.client("cloudformation", region_name=config.AWS_REGION)
        cfn.describe_stacks(StackName=config.PROJECT_NAME)
    except Exception:
        if not force:
            print(f"Account {account} does not own a {config.PROJECT_NAME} stack.")
            print("Refusing to delete. Re-run with --force if this is intended.")
            sys.exit(3)


def main(argv: list[str]) -> int:
    confirmed = "--yes" in argv
    force = "--force" in argv

    if config is None:
        print(f"No AWS credentials (or no deployed {os.environ.get('PROJECT_NAME', 'project')} "
              f"stack) found; nothing to discover.")
        print(f"  ({_CONFIG_ERROR})")
        steps = []
    else:
        steps = plan()

    print(f"\n{'DELETING' if confirmed else 'DRY RUN — would delete'}:\n")
    if not steps:
        print("  (nothing found)")
    for s in steps:
        print(f"  [{s['kind']:<22}] {s['name']}")

    if not confirmed:
        print("\nNothing was deleted. Re-run with --yes to delete.")
        return 0

    if config is None:
        print("\nCannot delete: AWS is unreachable.")
        return 1

    _guard_account(force)

    results = []
    for s in steps:
        try:
            _delete(s)
            results.append((s["name"], "deleted"))
        except Exception as exc:
            # One failure must not abort the rest - the point is to stop billing.
            results.append((s["name"], f"FAILED: {exc}"))

    print("\nSummary:")
    for name, outcome in results:
        print(f"  {outcome:<40} {name}")
    return 0 if all(o == "deleted" for _, o in results) else 1


def _delete(step: dict) -> None:
    """Delete one resource, using the matching delete_* API for its service.

    Raises on failure - main() catches it and continues to the next resource.
    """
    kind = step["kind"]
    name = step["name"]
    region = config.AWS_REGION

    if kind == "knowledge-base":
        agent = boto3.client("bedrock-agent", region_name=region)
        kbs = agent.list_knowledge_bases().get("knowledgeBaseSummaries", [])
        match = next((k for k in kbs if k["name"] == name), None)
        if match is None:
            raise RuntimeError(f"knowledge base {name!r} no longer exists")
        kb_id = match["knowledgeBaseId"]
        # Data sources must go before the Knowledge Base that owns them.
        data_sources = agent.list_data_sources(knowledgeBaseId=kb_id) \
            .get("dataSourceSummaries", [])
        for ds in data_sources:
            agent.delete_data_source(knowledgeBaseId=kb_id, dataSourceId=ds["dataSourceId"])
        agent.delete_knowledge_base(knowledgeBaseId=kb_id)

    elif kind == "s3-vectors":
        s3v = boto3.client("s3vectors", region_name=region)
        indexes = s3v.list_indexes(vectorBucketName=name).get("indexes", [])
        for idx in indexes:
            s3v.delete_index(vectorBucketName=name, indexName=idx["indexName"])
        s3v.delete_vector_bucket(vectorBucketName=name)

    elif kind == "agentcore-runtime":
        ctrl = boto3.client("bedrock-agentcore-control", region_name=region)
        runtimes = ctrl.list_agent_runtimes().get("agentRuntimes", [])
        match = next((r for r in runtimes if r["agentRuntimeName"] == name), None)
        if match is None:
            raise RuntimeError(f"agent runtime {name!r} no longer exists")
        ctrl.delete_agent_runtime(agentRuntimeId=match["agentRuntimeId"])

    elif kind == "agentcore-memory":
        ctrl = boto3.client("bedrock-agentcore-control", region_name=region)
        memories = ctrl.list_memories().get("memories", [])
        match = next(
            (m for m in memories if m.get("name") == name or m.get("id") == name),
            None,
        )
        if match is None:
            raise RuntimeError(f"AgentCore Memory {name!r} no longer exists")
        ctrl.delete_memory(memoryId=match.get("id", match.get("memoryId")))

    elif kind == "guardrail":
        br = boto3.client("bedrock", region_name=region)
        guardrails = br.list_guardrails().get("guardrails", [])
        match = next((g for g in guardrails if g["name"] == name), None)
        if match is None:
            raise RuntimeError(f"guardrail {name!r} no longer exists")
        br.delete_guardrail(guardrailIdentifier=match["id"])

    elif kind == "s3-objects":
        s3 = boto3.client("s3", region_name=region)
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=name):
            objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if objects:
                s3.delete_objects(Bucket=name, Delete={"Objects": objects})

    elif kind == "cloudformation-stack":
        cfn = boto3.client("cloudformation", region_name=region)
        cfn.delete_stack(StackName=name)

    else:
        raise NotImplementedError(kind)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
