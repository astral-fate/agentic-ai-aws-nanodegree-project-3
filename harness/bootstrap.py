"""
harness/bootstrap.py
=====================
Owns the import ordering the harness depends on.

config.py calls sts.get_caller_identity() at module scope, and
agent_orchestrator.py builds six boto3 clients (plus one more, xray, via
agent_utils/agent_observability's lazily-created clients) at module scope.
All of that runs on import, so moto and the strands stand-ins must be live
*before* the first `import config` or `import agent_orchestrator`.

Why a real CloudFormation stack (not env-var fallbacks):
config.py's `_get()` helper only consults CloudFormation exports for seven
resource identifiers - OrdersTable, CustomersTable, WorkflowStateTable,
PolicyBucket, VectorBucket, AgentCoreRoleArn, AgentLogGroup - and raises
ValueError at import time if the export is missing. It is called with no
env-var fallback for any of these, so setting environment variables alone
cannot satisfy it. Only the three Knowledge Base ids (`_get_kb_id`) have a
genuine env-var fallback path.

So this harness creates one real moto CloudFormation stack whose template
both provisions the DynamoDB tables / S3 buckets AND publishes the matching
`{PROJECT_NAME}-<Key>` exports in the same step (rather than creating
resources directly with boto3 and using the stack only to publish exports).
moto's CloudFormation backend supports AWS::DynamoDB::Table and
AWS::S3::Bucket plus Export-style Outputs cleanly, so one template gives us
both the resources and the exports config.py needs, instead of keeping two
sources of truth (a boto3-created table and a hand-built exports dict) in
sync by hand.
"""
import json
import os
import sys
import pathlib
import time

_mock = None
_module = None

PROJECT_NAME = "udacity-agentcore"
REGION = "us-east-1"

# Real table names the deployed project uses (infrastructure/starter_stack.yaml).
# config.py's _get() looks up CloudFormation export "{PROJECT_NAME}-<Key>" for
# each of these; the exported Value must be the actual resource name/ARN.
_ORDERS_TABLE_NAME         = f"{PROJECT_NAME}-orders"
_CUSTOMERS_TABLE_NAME      = f"{PROJECT_NAME}-customers"
_WORKFLOW_STATE_TABLE_NAME = f"{PROJECT_NAME}-workflow-state"
_POLICY_BUCKET_NAME        = f"{PROJECT_NAME}-policy-docs"
_VECTOR_BUCKET_NAME        = f"{PROJECT_NAME}-vectors"
_AGENTCORE_ROLE_ARN        = f"arn:aws:iam::123456789012:role/{PROJECT_NAME}-agentcore-role"
_AGENT_LOG_GROUP_NAME      = f"/{PROJECT_NAME}/agent-logs"

_STACK_NAME = f"{PROJECT_NAME}-offline-harness"


def _fake_credentials():
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
    os.environ.setdefault("AWS_DEFAULT_REGION", REGION)
    os.environ.setdefault("AWS_REGION", REGION)
    os.environ.setdefault("PROJECT_NAME", PROJECT_NAME)

    # The three Knowledge Base ids are the one place config.py genuinely
    # falls back to the environment (config._get_kb_id never raises), so
    # env vars alone are correct here - unlike the seven CloudFormation-only
    # exports handled by the stack below.
    os.environ.setdefault("RETURNS_KB_ID",  "KBRETURNS01")
    os.environ.setdefault("SHIPPING_KB_ID", "KBSHIPPING1")
    os.environ.setdefault("WARRANTY_KB_ID", "KBWARRANTY1")


def _cf_template() -> dict:
    """Build the CloudFormation template that both provisions the moto
    resources config.py expects AND publishes the exports it reads."""
    return {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Resources": {
            "OrdersTable": {
                "Type": "AWS::DynamoDB::Table",
                "Properties": {
                    "TableName": _ORDERS_TABLE_NAME,
                    "AttributeDefinitions": [
                        {"AttributeName": "customer_id", "AttributeType": "S"},
                        {"AttributeName": "order_id",    "AttributeType": "S"},
                    ],
                    "KeySchema": [
                        {"AttributeName": "customer_id", "KeyType": "HASH"},
                        {"AttributeName": "order_id",    "KeyType": "RANGE"},
                    ],
                    "BillingMode": "PAY_PER_REQUEST",
                },
            },
            "CustomersTable": {
                "Type": "AWS::DynamoDB::Table",
                "Properties": {
                    "TableName": _CUSTOMERS_TABLE_NAME,
                    "AttributeDefinitions": [
                        {"AttributeName": "customer_id", "AttributeType": "S"},
                    ],
                    "KeySchema": [
                        {"AttributeName": "customer_id", "KeyType": "HASH"},
                    ],
                    "BillingMode": "PAY_PER_REQUEST",
                },
            },
            "WorkflowStateTable": {
                "Type": "AWS::DynamoDB::Table",
                "Properties": {
                    "TableName": _WORKFLOW_STATE_TABLE_NAME,
                    "AttributeDefinitions": [
                        {"AttributeName": "session_id", "AttributeType": "S"},
                    ],
                    "KeySchema": [
                        {"AttributeName": "session_id", "KeyType": "HASH"},
                    ],
                    "BillingMode": "PAY_PER_REQUEST",
                },
            },
            "PolicyBucket": {
                "Type": "AWS::S3::Bucket",
                "Properties": {"BucketName": _POLICY_BUCKET_NAME},
            },
            "VectorBucket": {
                "Type": "AWS::S3::Bucket",
                "Properties": {"BucketName": _VECTOR_BUCKET_NAME},
            },
        },
        "Outputs": {
            "OrdersTableExport": {
                "Value": {"Ref": "OrdersTable"},
                "Export": {"Name": f"{PROJECT_NAME}-OrdersTable"},
            },
            "CustomersTableExport": {
                "Value": {"Ref": "CustomersTable"},
                "Export": {"Name": f"{PROJECT_NAME}-CustomersTable"},
            },
            "WorkflowStateTableExport": {
                "Value": {"Ref": "WorkflowStateTable"},
                "Export": {"Name": f"{PROJECT_NAME}-WorkflowStateTable"},
            },
            "PolicyBucketExport": {
                "Value": {"Ref": "PolicyBucket"},
                "Export": {"Name": f"{PROJECT_NAME}-PolicyBucket"},
            },
            "VectorBucketExport": {
                "Value": {"Ref": "VectorBucket"},
                "Export": {"Name": f"{PROJECT_NAME}-VectorBucket"},
            },
            # These two have no natural moto-managed resource behind them
            # (an IAM role ARN and a CloudWatch Logs group name), so they are
            # published as literal Output values rather than Ref/GetAtt.
            "AgentCoreRoleArnExport": {
                "Value": _AGENTCORE_ROLE_ARN,
                "Export": {"Name": f"{PROJECT_NAME}-AgentCoreRoleArn"},
            },
            "AgentLogGroupExport": {
                "Value": _AGENT_LOG_GROUP_NAME,
                "Export": {"Name": f"{PROJECT_NAME}-AgentLogGroup"},
            },
        },
    }


def _create_stack():
    import boto3

    cf = boto3.client("cloudformation", region_name=REGION)
    cf.create_stack(
        StackName=_STACK_NAME,
        TemplateBody=json.dumps(_cf_template()),
    )

    deadline = time.time() + 30
    while time.time() < deadline:
        status = cf.describe_stacks(StackName=_STACK_NAME)["Stacks"][0]["StackStatus"]
        if status.endswith("COMPLETE"):
            return
        if status.endswith("FAILED"):
            raise RuntimeError(f"moto CloudFormation stack failed: {status}")
        time.sleep(0.1)
    raise TimeoutError("moto CloudFormation stack did not reach *_COMPLETE in time")


def load_orchestrator():
    """Import the unmodified deliverable with every AWS call intercepted."""
    global _mock, _module
    if _module is not None:
        return _module

    _fake_credentials()

    from moto import mock_aws
    _mock = mock_aws()
    _mock.start()

    from harness import fakes
    fakes.register()
    fakes.register_boto_stubs()   # Bedrock/AgentCore/xray stubs; moto handles the rest

    _create_stack()

    src_dir  = pathlib.Path(__file__).resolve().parent.parent / "src"
    root_dir = pathlib.Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(src_dir))
    sys.path.insert(0, str(root_dir))

    fakes.patch_kb_retrieval()   # must precede the import below - see fakes.py

    import agent_orchestrator
    _module = agent_orchestrator
    return _module


def reset():
    """Tear down moto and drop cached imports so the next load starts clean."""
    global _mock, _module
    for name in (
        "agent_orchestrator", "config", "agent_utils",
        "bedrock_kb_retrieval", "agent_observability",
        "strands", "strands.models",
    ):
        sys.modules.pop(name, None)
    if _mock is not None:
        _mock.stop()
        _mock = None
    _module = None
