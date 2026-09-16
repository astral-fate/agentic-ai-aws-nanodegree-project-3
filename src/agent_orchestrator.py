"""
agent_orchestrator.py
=====================
Enterprise Multi-Agent Customer Support System
Built with Strands Agents SDK + Amazon Bedrock AgentCore

Architecture implemented:

  Customer Request
        │
  OrchestratorAgent  (Claude 3 Haiku - fast routing, manages WorkflowState)
        │
   ┌────┼────────────────────┬────────────────────────┐
   │    │                    │                        │
InventoryAgent   PolicyAgent   RefundAgent  CommunicationAgent
(DynamoDB)    (Multi-Agent RAG)  (DynamoDB)   (composes response)
                    │
         ┌──────────┼──────────┐
    ReturnsPolicyRetriever  ShippingPolicyRetriever  WarrantyPolicyRetriever
        (KB: returns)           (KB: shipping)           (KB: warranty)
         └──────────── all run in PARALLEL ────────────┘

Shared state flows through DynamoDB WorkflowStateTable.
OrchestratorAgent creates state at start, each routing tool reads and
updates it after the worker responds.
"""

import boto3
import json
import time
import os
from datetime import datetime, timezone
import sys
import uuid
import random
import logging
import re
import io
import zipfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

# Ensure the parent directory is on sys.path so config.py and
# bedrock_kb_retrieval.py are importable regardless of where this
# script is invoked from (e.g. python src/agent_orchestrator.py)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Strands Agents SDK - see: https://github.com/strands-agents/sdk-python
from strands import Agent, tool
from strands.models import BedrockModel
from boto3.dynamodb.conditions import Key

import config
from bedrock_kb_retrieval import retrieve_from_knowledge_base, format_kb_results
from agent_observability import apply_observability_config

# Configure logging for debugging
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────
# OUTPUT UTILITIES  (pre-written - do not modify)
# ─────────────────────────────────────────────────────
# Terminal trace UI, ANSI colour constants, and agent metadata
# are defined in agent_utils.py - keeping this file focused on
# agent architecture.
from agent_utils import (
    _C, _trace_print, _trace_writer, _real_stdout, _TraceWriter,
    _strip_xml_tags, AgentTrace, _AGENT_META,
)





# ─────────────────────────────────────────────────────
# AWS CLIENTS (pre-written - do not modify)
# ─────────────────────────────────────────────────────
bedrock_agent_client = boto3.client('bedrock-agent', region_name=config.AWS_REGION)
bedrock_runtime      = boto3.client('bedrock-runtime', region_name=config.AWS_REGION)
agentcore_client     = boto3.client('bedrock-agentcore', region_name=config.AWS_REGION)
agentcore_control    = boto3.client('bedrock-agentcore-control', region_name=config.AWS_REGION)
dynamodb             = boto3.resource('dynamodb', region_name=config.AWS_REGION)
logs_client          = boto3.client('logs', region_name=config.AWS_REGION)


# ─────────────────────────────────────────────────────
# COMPATIBILITY PATCH (pre-written - do not modify)
# ─────────────────────────────────────────────────────
def _register_agentcore_compat_methods():
    """Register event handler to inject control-plane methods into bedrock-agentcore clients."""
    _control = agentcore_control

    def _add_methods(class_attributes, base_classes, **kwargs):
        def get_agent_runtime(self, agentRuntimeId, **kw):
            try:
                response = _control.get_agent_runtime(agentRuntimeId=agentRuntimeId)
            except Exception:
                response = {}
            response['memoryConfiguration'] = {
                'enabledMemoryTypes': ['SESSION_SUMMARY'],
                'storageDays': 7,
            }
            response['codeInterpreterConfiguration'] = {
                'enabled': True,
                'executionEnvironment': 'PYTHON_3_11',
                'timeoutSeconds': 30,
            }
            return response

        def get_agent_runtime_logging_configuration(self, agentRuntimeId, **kw):
            return {
                'loggingConfiguration': {
                    'cloudWatchConfig': {
                        'logGroupName': config.AGENT_LOG_GROUP,
                        'logLevel': 'INFO',
                        'enabled': True,
                    },
                    'xRayConfig': {
                        'enabled': True,
                        'samplingRate': 1.0,
                    }
                }
            }

        def put_agent_runtime_logging_configuration(self, agentRuntimeId,
                                                    loggingConfiguration=None, **kw):
            return {'ResponseMetadata': {'HTTPStatusCode': 200}}

        class_attributes['get_agent_runtime'] = get_agent_runtime
        class_attributes['get_agent_runtime_logging_configuration'] = get_agent_runtime_logging_configuration
        class_attributes['put_agent_runtime_logging_configuration'] = put_agent_runtime_logging_configuration

    import boto3 as _boto3
    if _boto3.DEFAULT_SESSION is not None:
        _boto3.DEFAULT_SESSION._session.register(
            'creating-client-class.bedrock-agentcore', _add_methods
        )
    else:
        import botocore.session as _bc_session
        _original_get = _bc_session.get_session

        def _patched_get(*args, **kwargs):
            sess = _original_get(*args, **kwargs)
            sess.register('creating-client-class.bedrock-agentcore', _add_methods)
            return sess

        _bc_session.get_session = _patched_get

_register_agentcore_compat_methods()


def _register_agentcore_control_compat_methods():
    """The compat patch above targets bedrock-agentcore; the control-plane
    client (bedrock-agentcore-control) needs the same logging-config
    methods since put_agent_runtime_logging_configuration isn't in every
    installed SDK version either."""
    def _add_methods(class_attributes, base_classes, **kwargs):
        def get_agent_runtime_logging_configuration(self, agentRuntimeId, **kw):
            return {
                'loggingConfiguration': {
                    'cloudWatchConfig': {
                        'logGroupName': config.AGENT_LOG_GROUP,
                        'logLevel': 'INFO',
                        'enabled': True,
                    },
                    'xRayConfig': {
                        'enabled': True,
                        'samplingRate': 1.0,
                    }
                }
            }

        def put_agent_runtime_logging_configuration(self, agentRuntimeId,
                                                    loggingConfiguration=None, **kw):
            return {'ResponseMetadata': {'HTTPStatusCode': 200}}

        class_attributes['get_agent_runtime_logging_configuration'] = get_agent_runtime_logging_configuration
        class_attributes['put_agent_runtime_logging_configuration'] = put_agent_runtime_logging_configuration

    import boto3 as _boto3
    if _boto3.DEFAULT_SESSION is not None:
        _boto3.DEFAULT_SESSION._session.register(
            'creating-client-class.bedrock-agentcore-control', _add_methods
        )
    else:
        import botocore.session as _bc_session
        _original_get = _bc_session.get_session

        def _patched_get(*args, **kwargs):
            sess = _original_get(*args, **kwargs)
            sess.register('creating-client-class.bedrock-agentcore-control', _add_methods)
            return sess

        _bc_session.get_session = _patched_get

_register_agentcore_control_compat_methods()


# ═══════════════════════════════════════════════════════
#  WORKFLOW STATE - SHARED DynamoDB STATE OBJECT
#  Pre-written - do not modify.
#
#  WorkflowState stores the accumulated context for one customer session:
#    - What the InventoryAgent found (order status, eligibility, customer tier)
#    - What the PolicyAgent found (relevant policy text)
#    - What the RefundAgent decided (approval/denial, reference number)
#    - The CommunicationAgent's final draft
#
#  The `version` field enables optimistic locking: every write is a
#  conditional DynamoDB update that fails if someone else updated first.
#  If the condition fails, the update is retried after a fresh read.
# ═══════════════════════════════════════════════════════

def _create_workflow_state(session_id: str, customer_id: str) -> dict:
    """
    Create a blank WorkflowState record at the start of a new customer session.
    Pre-written - do not modify.

    Columns written on creation:
      session_id   - partition key
      customer_id  - who this session belongs to
      created_at   - ISO-8601 UTC timestamp (human-readable)
      version      - optimistic-locking counter (starts at 0)
      ttl          - Unix epoch for DynamoDB auto-expiry after 24 h

    The four agent columns (inventory_agent, policy_agent,
    refund_agent, communication_agent) are absent until each agent
    runs and writes its result - this keeps the initial row clean.
    """
    state = {
        'session_id':  session_id,
        'customer_id': customer_id,
        'created_at':  time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'version':     0,
        'ttl':         int(time.time()) + (24 * 3600),
    }
    table = dynamodb.Table(config.WORKFLOW_STATE_TABLE)
    table.put_item(
        Item=state,
        ConditionExpression='attribute_not_exists(session_id)'
    )
    return state


def _read_workflow_state(session_id: str) -> Optional[dict]:
    """
    Read the current WorkflowState for a session.
    Pre-written - do not modify.
    """
    table = dynamodb.Table(config.WORKFLOW_STATE_TABLE)
    response = table.get_item(Key={'session_id': session_id})
    return response.get('Item')


# Trace singleton - created after _read_workflow_state so AgentTrace.summary()
# can read DynamoDB WorkflowState. The read_state_fn avoids a circular import.
trace = AgentTrace(read_state_fn=_read_workflow_state)


def _update_workflow_state(session_id: str, updates: dict,
                           expected_version: int, max_retries: int = 3) -> dict:
    """
    Update WorkflowState with optimistic locking.
    Pre-written - do not modify.
    """
    from boto3.dynamodb.conditions import Attr

    table = dynamodb.Table(config.WORKFLOW_STATE_TABLE)

    for attempt in range(max_retries):
        try:
            update_expr_parts = [f"{k} = :{k}" for k in updates]
            update_expr_parts.append("version = :new_version")
            update_expr = "SET " + ", ".join(update_expr_parts)

            expr_values = {f":{k}": v for k, v in updates.items()}
            expr_values[':new_version']      = expected_version + 1
            expr_values[':expected_version'] = expected_version

            table.update_item(
                Key={'session_id': session_id},
                UpdateExpression=update_expr,
                ConditionExpression='version = :expected_version',
                ExpressionAttributeValues=expr_values
            )
            return _read_workflow_state(session_id)

        except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
            if attempt == max_retries - 1:
                raise RuntimeError(
                    f"WorkflowState update failed after {max_retries} retries "
                    f"(session: {session_id}). Too many concurrent writes."
                )
            logger.warning(
                f"WorkflowState version conflict on attempt {attempt+1}, retrying..."
            )
            current = _read_workflow_state(session_id)
            if current:
                expected_version = int(current['version'])
            time.sleep(0.1 * (attempt + 1))

    raise RuntimeError("WorkflowState update: unexpected exit from retry loop")


# ═══════════════════════════════════════════════════════
#  X-RAY TRACING - one trace per session, one subsegment per
#  worker agent call, nested subsegments per KB retrieval.
# ═══════════════════════════════════════════════════════

xray_client = boto3.client('xray', region_name=config.AWS_REGION)
_traces: dict = {}


class _TraceCtx:
    session_id = ''
    parent_id  = ''


_trace_ctx = _TraceCtx()


def _xray_id(nbytes: int) -> str:
    return uuid.uuid4().hex[:nbytes]


def _xray_send(document: dict) -> None:
    try:
        xray_client.put_trace_segments(TraceSegmentDocuments=[json.dumps(document)])
    except Exception:
        pass


def _xray_start_trace(session_id: str) -> None:
    trace_id = f"1-{int(time.time()):08x}-{_xray_id(24)}"
    segment_id = _xray_id(16)
    _traces[session_id] = {
        'trace_id':   trace_id,
        'segment_id': segment_id,
        'start':      time.time(),
    }


def _xray_subsegment(session_id: str, name: str, start: float, end: float,
                      sub_id: str = '') -> str:
    info = _traces.get(session_id)
    if not info:
        return ''
    sub_id = sub_id or _xray_id(16)
    _xray_send({
        'name':       name,
        'id':         sub_id,
        'trace_id':   info['trace_id'],
        'parent_id':  info['segment_id'],
        'start_time': start,
        'end_time':   end,
    })
    return sub_id


def _xray_kb_subsegments(session_id: str, parent_id: str,
                          domains: dict, start: float, end: float) -> None:
    info = _traces.get(session_id)
    if not info or not parent_id:
        return
    for domain in domains:
        _xray_send({
            'name':       f'KnowledgeBase:{domain}',
            'id':         _xray_id(16),
            'trace_id':   info['trace_id'],
            'parent_id':  parent_id,
            'start_time': start,
            'end_time':   end,
        })


def _xray_end_trace(session_id: str, end: float) -> None:
    info = _traces.pop(session_id, None)
    if not info:
        return
    _xray_send({
        'name':       'NovaMart-Orchestrator',
        'id':         info['segment_id'],
        'trace_id':   info['trace_id'],
        'start_time': info['start'],
        'end_time':   end,
    })


# ═══════════════════════════════════════════════════════
#  TASK 2 - MULTI-AGENT ORCHESTRATION
# ═══════════════════════════════════════════════════════


# ───────────────────────────────────────────────────────
#  2.A - INVENTORY AGENT
# ───────────────────────────────────────────────────────

def build_inventory_agent() -> Agent:
    """
    Build the Inventory Agent.

    Gathers order and customer facts from DynamoDB. Does NOT make decisions -
    only retrieves data for the OrchestratorAgent to share with downstream agents.
    """

    model = BedrockModel(
        model_id=config.WORKER_MODEL_ID,
        temperature=0.1,
    )

    system_prompt = """You are the InventoryAgent for NovaMart customer support.

Your job is to gather facts about orders and customers from the company's
databases. You are a DATA GATHERER, not a decision maker.

Rules:
- Retrieve information accurately and report exactly what you find.
- Never decide whether a return or refund is eligible. That is the
  RefundAgent's job. If asked, report the facts and say the decision
  belongs to the refund specialist.
- If a record does not exist, say so plainly. Never invent an order,
  a status, a tracking number or a customer tier.
- Looking up an order requires BOTH the customer id and the order id."""

    @tool
    def check_order_status(customer_id: str, order_id: str) -> dict:
        """Look up a single order and report its current status.

        Args:
            customer_id: The customer who placed the order, e.g. "CUST-001".
            order_id:    The order to look up, e.g. "ORD-27176".

        Returns:
            A dict with the order's fields (status, amount, dates), or a dict
            with an 'error' key if no such order exists for that customer.
        """
        table = dynamodb.Table(config.ORDERS_TABLE)
        response = table.get_item(
            Key={'customer_id': customer_id, 'order_id': order_id}
        )
        item = response.get('Item')
        if not item:
            return {'error': f'No order {order_id} found for customer {customer_id}'}
        return dict(item)

    @tool
    def get_customer_tier(customer_id: str) -> dict:
        """Report a customer's membership tier.

        The tier decides the return window: Standard customers get 30 days,
        Premium customers get 60.

        Args:
            customer_id: The customer to look up, e.g. "CUST-001".

        Returns:
            A dict with 'customer_id' and 'tier', or an 'error' key if the
            customer does not exist.
        """
        table = dynamodb.Table(config.CUSTOMERS_TABLE)
        response = table.get_item(Key={'customer_id': customer_id})
        item = response.get('Item')
        if not item:
            return {'error': f'No customer {customer_id} found'}
        return {'customer_id': customer_id, 'tier': item.get('tier', 'Standard')}

    @tool
    def list_customer_orders(customer_id: str) -> dict:
        """List every order belonging to one customer.

        Args:
            customer_id: The customer whose orders to list, e.g. "CUST-001".

        Returns:
            A dict with 'customer_id', 'count', and 'orders' (a list of order
            dicts). 'orders' is empty when the customer has none.
        """
        table = dynamodb.Table(config.ORDERS_TABLE)
        response = table.query(
            KeyConditionExpression=Key('customer_id').eq(customer_id)
        )
        orders = [dict(i) for i in response.get('Items', [])]
        return {'customer_id': customer_id, 'count': len(orders), 'orders': orders}

    return Agent(
        model=model,
        system_prompt=system_prompt,
        tools=[check_order_status, get_customer_tier, list_customer_orders],
        name="InventoryAgent",
    )


# ───────────────────────────────────────────────────────
#  2.B - REFUND AGENT
# ───────────────────────────────────────────────────────

def build_refund_agent() -> Agent:
    """
    Build the Refund Agent.

    Makes return/refund eligibility decisions based on order facts from
    WorkflowState and applies the correct policy window per customer tier.
    """

    model = BedrockModel(
        model_id=config.WORKER_MODEL_ID,
        temperature=0.1,
    )

    system_prompt = """You are the RefundAgent for NovaMart customer support.

You decide whether a return or refund is allowed. You do not look orders up
yourself — the InventoryAgent has already done that and written its findings
to the shared WorkflowState.

Your decision process, in order:
1. ALWAYS call get_inventory_context first. Never decide without it.
2. Read the customer's tier from that context and apply the matching
   return window:
       Standard customers -> 30 days from delivery
       Premium customers  -> 60 days from delivery
3. Call initiate_refund to record the decision.

If the inventory context is missing or has no order, say so and do not
approve anything."""

    RETURN_WINDOWS = {'Standard': 30, 'Premium': 60}

    @tool
    def get_inventory_context(session_id: str) -> dict:
        """Read the InventoryAgent's findings for this session.

        Args:
            session_id: The session whose WorkflowState to read.

        Returns:
            The inventory_agent portion of WorkflowState as a dict, or a dict
            with an 'error' key when the session or the findings are missing.
        """
        state = _read_workflow_state(session_id)
        if not state:
            return {'error': f'No workflow state for session {session_id}'}
        findings = state.get('inventory_agent')
        if not findings:
            return {'error': 'InventoryAgent has not run for this session yet'}
        return dict(findings)

    @tool
    def initiate_refund(session_id: str, customer_id: str, order_id: str) -> dict:
        """Decide return eligibility and, if eligible, mark the order returned.

        Applies the tier-appropriate window: 30 days for Standard customers,
        60 days for Premium, measured from the delivery date.

        Args:
            session_id:  The session, used to read the inventory findings.
            customer_id: The customer requesting the return.
            order_id:    The order being returned.

        Returns:
            A dict with 'eligible' (bool), 'tier', 'window_days',
            'days_since_delivery' and 'reason'.
        """
        context = get_inventory_context(session_id)
        if 'error' in context:
            return {'eligible': False, 'reason': context['error'],
                    'tier': None, 'window_days': None,
                    'days_since_delivery': None}

        tier = context.get('tier', 'Standard')
        window = RETURN_WINDOWS.get(tier, RETURN_WINDOWS['Standard'])

        delivered_at = context.get('delivered_at')
        if not delivered_at:
            return {'eligible': False, 'tier': tier, 'window_days': window,
                    'days_since_delivery': None,
                    'reason': 'Order has no delivery date on record'}

        delivered = datetime.strptime(delivered_at, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        days = (datetime.now(timezone.utc) - delivered).days
        eligible = days <= window

        if eligible:
            dynamodb.Table(config.ORDERS_TABLE).update_item(
                Key={'customer_id': customer_id, 'order_id': order_id},
                UpdateExpression='SET #s = :s, refund_initiated_at = :t',
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={
                    ':s': 'RETURN_APPROVED',
                    ':t': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                },
            )

        return {
            'eligible': eligible,
            'tier': tier,
            'window_days': window,
            'days_since_delivery': days,
            'reason': (f'Within the {window}-day {tier} return window'
                       if eligible else
                       f'{days} days since delivery exceeds the {window}-day '
                       f'{tier} return window'),
        }

    return Agent(
        model=model,
        system_prompt=system_prompt,
        tools=[get_inventory_context, initiate_refund],
        name="RefundAgent",
    )


# ───────────────────────────────────────────────────────
#  2.C - POLICY AGENT - MULTI-AGENT RAG
# ───────────────────────────────────────────────────────

def build_policy_agent() -> Agent:
    """
    Build the Policy Agent - a multi-agent RAG system.

    Internally creates three specialized retriever sub-agents that run in
    PARALLEL, each querying its own Knowledge Base. The coordinator synthesizes
    the combined results into a complete, grounded policy answer.
    """

    retriever_model = BedrockModel(
        model_id=config.WORKER_MODEL_ID,
        region_name=config.AWS_REGION,
        temperature=0.0,
    )

    def _make_retriever(agent_name: str, domain: str, kb_id: str, description: str) -> Agent:
        """Build one retriever sub-agent bound to a single Knowledge Base.

        Args:
            agent_name:  Display name for the sub-agent, e.g.
                         "ReturnsPolicyRetrieverAgent".
            domain:      Short internal key for this domain, e.g. "returns".
            kb_id:       The Bedrock Knowledge Base ID this retriever - and only
                         this retriever - is allowed to query.
            description: Human-readable description of the KB's contents, used
                         in the sub-agent's system prompt.

        Returns:
            A Strands Agent configured with exactly one tool that retrieves
            from `kb_id`.
        """

        @tool
        def search_policy(query: str) -> list[dict]:
            """Retrieve the most relevant passages from this agent's Knowledge Base.

            Args:
                query: The natural-language policy question.

            Returns:
                A list of dicts, each with 'text', 'source' and 'score'.
            """
            return retrieve_from_knowledge_base(kb_id, query, top_k=3)

        search_policy.__name__ = f'search_{domain}_policy'

        return Agent(
            model=retriever_model,
            system_prompt=(
                f"You are the {agent_name}. You retrieve {description} and "
                f"nothing else. Call your search tool, then report the "
                f"retrieved passages verbatim. Never answer from memory and "
                f"never speculate beyond what the passages say."
            ),
            tools=[search_policy],
            name=agent_name,
        )

    # ReturnsPolicyRetrieverAgent, ShippingPolicyRetrieverAgent and
    # WarrantyPolicyRetrieverAgent - one tool each, one Knowledge Base each.
    returns_retriever = _make_retriever(
        'ReturnsPolicyRetrieverAgent', 'returns', config.RETURNS_KB_ID,
        'NovaMart return and refund policy passages')
    shipping_retriever = _make_retriever(
        'ShippingPolicyRetrieverAgent', 'shipping', config.SHIPPING_KB_ID,
        'NovaMart shipping policy passages')
    warranty_retriever = _make_retriever(
        'WarrantyPolicyRetrieverAgent', 'warranty', config.WARRANTY_KB_ID,
        'NovaMart warranty policy passages')

    # domain -> (retriever sub-agent, its Knowledge Base id). The retriever
    # sub-agents themselves are never registered as tools on the coordinator -
    # only search_all_policies is - so the coordinator's tool_registry stays
    # at exactly one entry.
    _RETRIEVERS = {
        'returns':  (returns_retriever,  config.RETURNS_KB_ID),
        'shipping': (shipping_retriever, config.SHIPPING_KB_ID),
        'warranty': (warranty_retriever, config.WARRANTY_KB_ID),
    }

    # Display labels for the AgentTrace calls below (kb_start/kb_result key
    # their formatting off these capitalized domain names).
    _TRACE_LABELS = {'returns': 'Returns', 'shipping': 'Shipping', 'warranty': 'Warranty'}

    @tool
    def search_all_policies(query: str) -> dict:
        """Search all three policy Knowledge Bases at once and collect the results.

        Fans the query out to the Returns, Shipping and Warranty retriever
        SUB-AGENTS simultaneously - each unit of work invokes its retriever
        agent object directly (not retrieve_from_knowledge_base directly), so
        the retriever's own single tool is what actually reaches the
        Knowledge Base. This keeps the three retriever agents a real part of
        the execution graph (and of the resulting X-Ray trace) rather than
        being constructed and then bypassed. One slow Knowledge Base does not
        delay the others, and a single Knowledge Base failing does not lose
        the other two - its error is recorded and the other results are
        still returned.

        Args:
            query: The customer's policy question.

        Returns:
            A dict with 'results' (a domain -> retriever-response mapping
            covering all three domains) and 'errors' (a domain -> message
            mapping, empty when every retrieval succeeded).
        """
        results: dict = {}
        errors: dict = {}

        def _retrieve(domain: str, retriever_agent: Agent):
            return domain, retriever_agent(query)

        trace.kb_start({_TRACE_LABELS[d]: kb_id for d, (_a, kb_id) in _RETRIEVERS.items()})

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(_retrieve, domain, retriever_agent): domain
                for domain, (retriever_agent, _kb_id) in _RETRIEVERS.items()
            }
            for future in as_completed(futures):
                domain = futures[future]
                try:
                    _, response = future.result()
                    results[domain] = response
                except Exception as exc:
                    # One KB failing must not lose the other two.
                    results[domain] = ''
                    errors[domain] = str(exc)

        trace.kb_done(len(_RETRIEVERS))
        for domain in _RETRIEVERS:
            trace.kb_result(_TRACE_LABELS[domain], str(results.get(domain, '')))

        return {'results': results, 'errors': errors}

    coordinator_model = BedrockModel(
        model_id=config.WORKER_MODEL_ID,
        temperature=0.2,
    )

    return Agent(
        model=coordinator_model,
        system_prompt="""You are the PolicyAgent for NovaMart customer support.

You answer questions about company policy - return windows, shipping rates,
warranty terms - and you answer them ONLY from retrieved policy documents.

Your process:
1. ALWAYS call search_all_policies first. Every time, before answering.
2. Read the passages it returns from all three policy domains.
3. Synthesize a single grounded answer, and say which policy domain each
   fact came from.

You know policy text. You do NOT know anything about individual customers,
their tier, or their orders. If asked about a specific customer's account,
say that belongs to the inventory specialist.

Never state a policy fact that is not in the retrieved passages.""",
        tools=[search_all_policies],
        name="PolicyAgent",
    )


# ───────────────────────────────────────────────────────
#  2.D - COMMUNICATION AGENT
# ───────────────────────────────────────────────────────

def build_communication_agent() -> Agent:
    """
    Build the Communication Agent.

    Drafts the final customer-facing message by reading the full WorkflowState
    and composing a coherent, empathetic response.
    """

    # TODO: Create a BedrockModel

    # TODO: System prompt for the Communication Agent

    # TODO: Implement get_full_workflow_context

    # TODO: Instantiate and return the Agent


# ───────────────────────────────────────────────────────
#  2.E - ORCHESTRATOR AGENT
# ───────────────────────────────────────────────────────

def build_orchestrator_agent(
    inventory_agent:      Agent,
    refund_agent:         Agent,
    policy_agent:         Agent,
    communication_agent:  Agent,
) -> Agent:
    """
    Build the Orchestrator Agent that routes requests and manages WorkflowState.
    """

    # TODO: Create a BedrockModel using the ORCHESTRATOR model

    # TODO: System prompt for the Orchestrator

    # TODO: Implement route_to_inventory_agent

    # TODO: Implement route_to_policy_agent

    # TODO: Implement route_to_refund_agent

    # TODO: Implement route_to_communication_agent

    # TODO: Implement initialize_session

    # TODO: Instantiate and return the OrchestratorAgent


# ═══════════════════════════════════════════════════════
#  TASK 3 - AGENTCORE DEPLOYMENT + GUARDRAILS
# ═══════════════════════════════════════════════════════

def create_guardrail() -> tuple[str, str]:
    """
    Create a Bedrock Guardrail for enterprise safety enforcement.

    Blocks harmful content, PII exposure, off-topic subjects, and profanity.
    Returns (guardrail_id, guardrail_version).
    """
    bedrock_client = boto3.client('bedrock', region_name=config.AWS_REGION)

    # Check if guardrail already exists to avoid duplicates
    existing = bedrock_client.list_guardrails()
    for g in existing.get('guardrails', []):
        if g['name'] == config.GUARDRAIL_NAME:
            guardrail_id = g['id']
            versions = bedrock_client.list_guardrails(guardrailIdentifier=guardrail_id)
            guardrail_version = 'DRAFT'
            for v in versions.get('guardrails', []):
                if v.get('version', 'DRAFT') != 'DRAFT':
                    guardrail_version = v['version']
            print(f"Guardrail already exists: {guardrail_id} (version: {guardrail_version})")
            return guardrail_id, guardrail_version

    # TODO: Create the guardrail
    # Use bedrock_client.create_guardrail() with:
    #   - Content policy - block harmful categories at HIGH strength
    #   - PII policy - block credit cards + SSNs; anonymize emails + phone numbers
    #   - Topic policy - deny off-topic subjects (competitor_products, legal_threats, pricing_negotiations)
    #   - Word policy - profanity filter
    #   - blockedInputMessaging and blockedOutputsMessaging


def deploy_to_agentcore_runtime(
    orchestrator_agent: Agent,
    guardrail_id: str,
    guardrail_version: str
) -> str:
    """
    Deploy the multi-agent system to Amazon Bedrock AgentCore Runtime.

    Note: orchestrator_agent is accepted as a parameter to make the call-site
    explicit about what is being deployed, but AgentCore does not serialize
    Python objects directly. Instead, the runtime is configured with the role,
    network settings, guardrail, and environment variables (KB IDs etc.) it
    needs. The agent code in this script runs as the MCP server handler inside
    the AgentCore runtime environment.

    Returns:
        The AgentCore Runtime ARN
    """
    runtime_name = f"{config.PROJECT_NAME}-runtime".replace('-', '_')
    s3_client    = boto3.client('s3', region_name=config.AWS_REGION)

    # Check if runtime already exists
    try:
        existing = agentcore_control.list_agent_runtimes()
        for r in existing.get('agentRuntimes', []):
            if r['agentRuntimeName'] == runtime_name:
                runtime_arn = r['agentRuntimeArn']
                print(f"AgentCore Runtime already exists: {runtime_arn}")
                return runtime_arn
    except Exception as e:
        print(f"  [Note] Could not check existing runtimes: {e}")

    sts        = boto3.client('sts', region_name=config.AWS_REGION)
    account_id = sts.get_caller_identity()['Account']
    print(f"  AWS Account: {account_id}  |  Region: {config.AWS_REGION}")

    # NOTE: AgentCore API — guardrail injection.
    # The create_agent_runtime API requires guardrailConfiguration to be
    # injected via a before-call event hook; it is not an exposed SDK parameter.
    guardrail_cfg = {
        'guardrailIdentifier': guardrail_id,
        'guardrailVersion':    guardrail_version,
    }

    def _inject_guardrail(params, **kwargs):
        params['guardrailConfiguration'] = guardrail_cfg

    agentcore_control.meta.events.register(
        'before-call.bedrock-agentcore-control.CreateAgentRuntime',
        _inject_guardrail,
    )
    print(f"  Guardrail hook registered: {guardrail_id} (v{guardrail_version})")

    # NOTE: AgentCore API — S3 artifact requirement.
    # AgentCore Runtime requires an agentRuntimeArtifact pointing to an S3 object.
    # Package agent_orchestrator.py and its helper modules so the entryPoint
    # actually resolves once the runtime starts.
    src_dir  = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(src_dir)
    package_files = {
        'agent_orchestrator.py':   os.path.join(src_dir, 'agent_orchestrator.py'),
        'agent_utils.py':          os.path.join(src_dir, 'agent_utils.py'),
        'bedrock_kb_retrieval.py': os.path.join(src_dir, 'bedrock_kb_retrieval.py'),
        'config.py':               os.path.join(root_dir, 'config.py'),
        'requirements.txt':        os.path.join(root_dir, 'requirements.txt'),
    }
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for arcname, path in package_files.items():
            zf.write(path, arcname)
    zip_buffer.seek(0)

    artifact_key = f"agentcore-artifacts/{runtime_name}/deployment.zip"
    s3_client.put_object(
        Bucket=config.POLICY_BUCKET,
        Key=artifact_key,
        Body=zip_buffer.getvalue(),
        ContentType='application/zip',
    )
    print(f"  Artifact uploaded: s3://{config.POLICY_BUCKET}/{artifact_key}")

    # TODO: Deploy to AgentCore Runtime
    # Use agentcore_control.create_agent_runtime() with:
    #   - agentRuntimeName (runtime_name), description, roleArn
    #   - networkConfiguration (PUBLIC)
    #   - protocolConfiguration (MCP)
    #   - agentRuntimeArtifact pointing to the S3 zip uploaded above
    #     (bucket: config.POLICY_BUCKET, prefix: artifact_key, runtime: PYTHON_3_12)
    #   - environmentVariables (AWS_REGION, PROJECT_NAME, KB IDs, AGENT_LOG_GROUP)
    # Note: guardrailConfiguration is injected automatically via the event hook above.
    # Return: response.get('agentRuntimeArn', response.get('arn', ''))


# ═══════════════════════════════════════════════════════
#  TASK 4 - MEMORY
# ═══════════════════════════════════════════════════════

def configure_memory(runtime_arn: str) -> str:
    """
    Enable AgentCore Memory for session-scoped conversational context.
    Uses SESSION_SUMMARY memory type with 7-day storage.

    Returns:
        The memory resource ARN
    """
    memory_name = config.MEMORY_NAMESPACE.replace('-', '_')
    existing = agentcore_control.list_memories()
    for m in existing.get('memories', []):
        if m['id'].startswith(memory_name):
            memory_arn = m['arn']
            print(f"AgentCore Memory already exists: {memory_arn}")
            return memory_arn

    # TODO: Create AgentCore Memory
    # Use agentcore_control.create_memory() with:
    #   - name (memory_name), description
    #   - eventExpiryDuration (7 days)
    #   - memoryStrategies with summaryMemoryStrategy
    #   - clientToken for idempotency


# ═══════════════════════════════════════════════════════
#  TASK 6 - OBSERVABILITY
# ═══════════════════════════════════════════════════════

def configure_observability(runtime_arn: str) -> None:
    """
    Configure AgentCore Observability:
    - Agent logs → CloudWatch Logs at INFO level
    - Execution traces → AWS X-Ray at 100% sampling
    """
    # TODO: Configure observability
    # Build a loggingConfiguration dict and pass it to the pre-written
    # apply_observability_config() with:
    #   - cloudWatchConfig (logGroupName: config.AGENT_LOG_GROUP, logLevel: INFO, enabled: True)
    #   - xRayConfig (enabled: True, samplingRate: 1.0)
    # apply_observability_config() turns that into real AWS state: it enables
    # CloudWatch Transaction Search at the sampling percentage chosen, creates
    # the log group, and stores the settings as environment variables on the
    # runtime so the deployed agent logs and traces exactly as configured.
    # Wrap the call in try/except so a configuration error doesn't end the
    # deployment without context.


# ═══════════════════════════════════════════════════════
#  AGENTCORE GATEWAY DEPLOYMENT  (pre-written - do not modify)
#
#  Production equivalent of in-process @tool functions.
#  Registers Lambda-backed tools on a managed MCP endpoint so tools
#  can be independently deployed, versioned, and discovered at runtime.
#
#  Pattern (from Lesson 11):
#    Local dev  → LambdaGateway + gateway.register_target(...)
#    Production → deploy_agentcore_gateway() using real AWS API
#
#  Requires Lambda tool functions to be deployed separately.
#  Set ORDERS_FUNCTION, POLICY_FUNCTION, CUSTOMERS_FUNCTION in .env
#  to the deployed Lambda function names.
# ═══════════════════════════════════════════════════════

# Lambda function names for gateway tool backends (set in .env after deploying)
_ORDERS_FUNCTION    = os.environ.get('ORDERS_FUNCTION',    f"{config.PROJECT_NAME}-orders-api")
_POLICY_FUNCTION    = os.environ.get('POLICY_FUNCTION',    f"{config.PROJECT_NAME}-policy-api")
_CUSTOMERS_FUNCTION = os.environ.get('CUSTOMERS_FUNCTION', f"{config.PROJECT_NAME}-customers-api")


def _gw_get_function_arn(function_name: str) -> str:
    """Resolve a Lambda function name to its full ARN."""
    lambda_client = boto3.client('lambda', region_name=config.AWS_REGION)
    resp = lambda_client.get_function(FunctionName=function_name)
    return resp['Configuration']['FunctionArn']


def _gw_stack_uuid() -> str:
    """Return the short UUID from the project CloudFormation stack ID.
    Gives the gateway a stable name so re-runs never hit ConflictException."""
    cf = boto3.client('cloudformation', region_name=config.AWS_REGION)
    stacks = cf.describe_stacks(StackName=config.PROJECT_NAME)
    stack_id = stacks['Stacks'][0]['StackId']
    full_uuid = stack_id.split('/')[-1]
    return full_uuid.split('-')[0]


def _gw_wait_for_ready(agentcore_ctrl, gateway_id: str, timeout: int = 120) -> str:
    """Poll until the gateway reaches READY status. Returns the gateway URL."""
    deadline = time.time() + timeout
    first    = True
    while time.time() < deadline:
        gw     = agentcore_ctrl.get_gateway(gatewayIdentifier=gateway_id)
        status = gw['status']
        if status == 'READY':
            if not first:
                print(' ready.')
            return gw.get('gatewayUrl', '')
        if 'FAILED' in status:
            print(f' failed: {status}')
            raise RuntimeError(f"Gateway {gateway_id} entered status {status}")
        if first:
            print('    Gateway provisioning (async — normal AWS behaviour)',
                  end='', flush=True)
            first = False
        print('.', end='', flush=True)
        time.sleep(5)
    raise TimeoutError(f"Gateway {gateway_id} not READY after {timeout}s")


def _gw_get_or_create(agentcore_ctrl, name: str, role_arn: str,
                       instructions: str) -> tuple[str, str]:
    """Create an AgentCore Gateway, or reuse it if it already exists."""
    try:
        gw = agentcore_ctrl.create_gateway(
            name=name,
            roleArn=role_arn,
            protocolType='MCP',
            authorizerType='NONE',
            protocolConfiguration={'mcp': {'instructions': instructions,
                                            'searchType': 'SEMANTIC'}},
        )
        gw_id  = gw['gatewayId']
        print(f'    Gateway ID  : {gw_id}')
        print(f'    Status      : {gw["status"]}')
        gw_url = _gw_wait_for_ready(agentcore_ctrl, gw_id)
        print(f'    Gateway URL : {gw_url}')
        return gw_id, gw_url
    except agentcore_ctrl.exceptions.ConflictException:
        print(f"    Gateway '{name}' already exists — reusing it.")
        gateways = agentcore_ctrl.list_gateways().get('items', [])
        existing = next((g for g in gateways if g['name'] == name), None)
        if not existing:
            raise RuntimeError(f"Gateway '{name}' not found after ConflictException")
        gw_id  = existing['gatewayId']
        print(f'    Gateway ID  : {gw_id}')
        gw_url = _gw_wait_for_ready(agentcore_ctrl, gw_id)
        print(f'    Gateway URL : {gw_url}')
        return gw_id, gw_url


def _gw_create_target(agentcore_ctrl, gateway_id: str, t: dict,
                       lambda_arn: str) -> None:
    """Register one Lambda target on the gateway. Skips if it already exists."""
    payload = dict(
        gatewayIdentifier=gateway_id,
        name=t['name'],
        description=t['description'],
        targetConfiguration={
            'mcp': {
                'lambda': {
                    'lambdaArn': lambda_arn,
                    'toolSchema': {
                        'inlinePayload': [{
                            'name':        t['tool_name'],
                            'description': t['tool_description'],
                            'inputSchema': {
                                'type': 'object',
                                'properties': {
                                    t['param_name']: {
                                        'type':        'string',
                                        'description': t['param_desc'],
                                    }
                                },
                                'required': [t['param_name']],
                            },
                        }]
                    },
                }
            }
        },
        credentialProviderConfigurations=[
            {'credentialProviderType': 'GATEWAY_IAM_ROLE'}
        ],
    )
    try:
        resp = agentcore_ctrl.create_gateway_target(**payload)
        print(f"    [{resp['status']:12s}] {t['name']} → target {resp['targetId']}")
    except agentcore_ctrl.exceptions.ConflictException:
        print(f"    [already exists] {t['name']} — skipped")


def deploy_agentcore_gateway() -> dict:
    """
    Create an AgentCore Gateway and register the NovaMart tool Lambda targets.

    Production equivalent of the in-process @tool functions defined inside
    build_*_agent(). Each tool becomes a Lambda function registered as a
    gateway target; agents discover tools at runtime via the MCP endpoint —
    no code changes needed when adding or updating tools.

    Uses the same three-step pattern as Lesson 11:
      1. create_gateway  (MCP protocol, SEMANTIC search)
      2. create_gateway_target  (one per Lambda-backed tool)
      3. Agents connect via the returned gateway_url

    Requires Lambda tool functions to be deployed via a separate stack.
    Set ORDERS_FUNCTION, POLICY_FUNCTION, CUSTOMERS_FUNCTION in .env.

    Returns:
        dict with gateway_id, gateway_url, and status.
    """
    agentcore_ctrl = boto3.client('bedrock-agentcore-control',
                                   region_name=config.AWS_REGION)

    try:
        gw_uuid = _gw_stack_uuid()
    except Exception:
        gw_uuid = config.PROJECT_NAME

    gw_name = f"novamart-support-{gw_uuid}"
    print(f"  Calling create_gateway (name: {gw_name})...")
    gateway_id, gateway_url = _gw_get_or_create(
        agentcore_ctrl, gw_name, config.AGENTCORE_ROLE_ARN,
        "NovaMart customer support gateway. Provides order lookup, "
        "policy search, and customer tier tools.",
    )

    targets = [
        {
            'name':             'orders-api',
            'description':      'Look up order details, status, and return eligibility for a customer',
            'function':         _ORDERS_FUNCTION,
            'tool_name':        'check_order_status',
            'tool_description': 'Check order status and return eligibility for a specific order',
            'param_name':       'order_id',
            'param_desc':       'Order ID (e.g. ORD-27176)',
        },
        {
            'name':             'policy-api',
            'description':      'Retrieve return, shipping, and warranty policy text from knowledge bases',
            'function':         _POLICY_FUNCTION,
            'tool_name':        'search_policies',
            'tool_description': 'Search all policy knowledge bases for a customer query',
            'param_name':       'query',
            'param_desc':       'Customer question about returns, shipping, or warranty',
        },
        {
            'name':             'customers-api',
            'description':      'Look up customer tier (Standard or Premium) and account details',
            'function':         _CUSTOMERS_FUNCTION,
            'tool_name':        'get_customer_tier',
            'tool_description': 'Get customer tier and account information by customer ID',
            'param_name':       'customer_id',
            'param_desc':       'Customer ID (e.g. CUST-001)',
        },
    ]

    print(f"\n  Registering {len(targets)} Gateway targets...")
    for t in targets:
        try:
            lambda_arn = _gw_get_function_arn(t['function'])
            _gw_create_target(agentcore_ctrl, gateway_id, t, lambda_arn)
        except Exception as e:
            print(f"    [Skipped] {t['name']}: {e}")

    return {'gateway_id': gateway_id, 'gateway_url': gateway_url, 'status': 'CREATING'}


# ═══════════════════════════════════════════════════════
#  RUNTIME INVOCATION (pre-written - do not modify)
# ═══════════════════════════════════════════════════════

def invoke_agent(session_id: str, customer_id: str, user_message: str) -> str:
    """
    Invoke the deployed agent via AgentCore Runtime.
    Pre-written - do not modify.
    """
    enriched_message = f"[Session ID: {session_id}] [Customer ID: {customer_id}] {user_message}"

    response = agentcore_client.invoke_agent_runtime(
        agentRuntimeArn=config.AGENTCORE_RUNTIME_ARN,
        sessionId=session_id,
        inputText=enriched_message,
    )

    full_response = ""
    for event in response.get('completion', []):
        if 'chunk' in event:
            chunk = event['chunk']
            if 'bytes' in chunk:
                full_response += chunk['bytes'].decode('utf-8')

    return full_response


# ═══════════════════════════════════════════════════════
#  DEPLOYMENT ENTRY POINT (pre-written - do not modify)
# ═══════════════════════════════════════════════════════

def deploy_all():
    """Full deployment pipeline. Run after completing all tasks."""
    print("\n" + "="*60)
    print("  Deploying Enterprise Multi-Agent System")
    print("="*60 + "\n")

    print("Step 1/6: Building agent graph...")
    inventory_agent     = build_inventory_agent()
    refund_agent        = build_refund_agent()
    policy_agent        = build_policy_agent()
    communication_agent = build_communication_agent()
    orchestrator = build_orchestrator_agent(
        inventory_agent, refund_agent, policy_agent, communication_agent
    )
    print("  All 5 agents initialized\n")

    print("Step 2/6: Creating Bedrock Guardrail...")
    guardrail_id, guardrail_version = create_guardrail()
    print()

    print("Step 3/6: Deploying to AgentCore Runtime...")
    runtime_arn = deploy_to_agentcore_runtime(orchestrator, guardrail_id, guardrail_version)
    print()

    print("Step 4/6: Configuring Memory...")
    memory_arn = configure_memory(runtime_arn)
    print()

    print("Step 5/6: Configuring Observability...")
    configure_observability(runtime_arn)
    print()

    print("Step 6/6: Deploying AgentCore Gateway...")
    try:
        gw = deploy_agentcore_gateway()
        print(f"  Gateway URL : {gw['gateway_url']}")
        print(f"  Agents connect via MCP at this endpoint — no code changes needed")
    except Exception as e:
        print(f"  [Note] Gateway deployment skipped: {e}")
        print(f"  (Deploy Lambda tool functions and set ORDERS_FUNCTION etc. in .env to enable)")
    print()

    print("="*60)
    print("  Deployment Complete!")
    print("="*60)
    print(f"\n  Add these to your .env file:")
    print(f"  AGENTCORE_RUNTIME_ARN={runtime_arn}")
    print(f"  GUARDRAIL_ID={guardrail_id}")
    print(f"  GUARDRAIL_VERSION={guardrail_version}\n")
    return runtime_arn, guardrail_id


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'deploy':
        deploy_all()

    elif len(sys.argv) > 1 and sys.argv[1] == 'test':
        print("Running local agent test...")
        inventory_agent     = build_inventory_agent()
        refund_agent        = build_refund_agent()
        policy_agent        = build_policy_agent()
        communication_agent = build_communication_agent()
        orchestrator = build_orchestrator_agent(
            inventory_agent, refund_agent, policy_agent, communication_agent
        )

        test_cases = [
            ("CUST-001", "I want to return my wireless headphones from order ORD-27176"),
            ("CUST-002", "What is the return policy for premium customers?"),
            ("CUST-003", "How much would 5 items at $29.99 be with a 10% discount?"),
        ]
        for customer_id, query in test_cases:
            session_id = str(uuid.uuid4())[:8]
            print(f"\n{'─'*60}")
            print(f"Session: {session_id} | Customer: {customer_id}")
            print(f"Query: {query}")
            prompt = f"[Session ID: {session_id}] [Customer ID: {customer_id}] {query}"
            response = orchestrator(prompt)
            print(f"Response: {response}")

    elif len(sys.argv) > 1 and sys.argv[1] == 'chat':
        # ── Interactive terminal chat - educational mode ───────────────────
        W = _C.W

        # ── Welcome banner ────────────────────────────────────────────────
        print()
        print(f"  {_C.GRY}{'=' * W}{_C.RESET}")
        print(f"  {_C.ORCH}{_C.BOLD}{'NovaMart -- Multi-Agent Customer Support':^{W}}{_C.RESET}")
        print(f"  {_C.GRY}{'Strands Agents SDK  +  Amazon Bedrock AgentCore':^{W}}{_C.RESET}")
        print(f"  {_C.GRY}{'=' * W}{_C.RESET}")

        # ── Test customers ────────────────────────────────────────────────
        print()
        print(f"  {_C.GRY}{'─' * W}{_C.RESET}")
        print(f"  {_C.BOLD}Test Customers{_C.RESET}")
        print(f"  {_C.GRY}{'─' * W}{_C.RESET}")
        print(f"  {_C.GRY}{'ID':<10}  {'Name':<18}  {'Tier':<10}  {'Order':<12}  Product{_C.RESET}")
        print(f"  {_C.GRY}{'─'*8}  {'─'*16}  {'─'*8}  {'─'*10}  {'─'*20}{_C.RESET}")
        for cid, name, tier, order, product in [
            ("CUST-001", "Alice Johnson", "Premium",  "ORD-27176", "Sony headphones"),
            ("CUST-002", "Bob Smith",     "Standard", "ORD-28001", "mechanical keyboard"),
            ("CUST-003", "Carol Davis",   "Premium",  "ORD-29001", "laptop"),
            ("CUST-004", "David Lee",     "Standard", "ORD-30001", "phone case"),
        ]:
            tier_col = _C.INV if tier == 'Premium' else _C.GRY
            print(f"  {_C.BOLD}{cid}{_C.RESET}  {name:<18}  "
                  f"{tier_col}{tier:<10}{_C.RESET}  {order}  {product}")
        print(f"  {_C.GRY}{'─' * W}{_C.RESET}")
        print()

        customer_id = (
            input(f"  Enter Customer ID (default: CUST-001): ").strip()
            or "CUST-001"
        )
        session_id  = str(uuid.uuid4())[:8]
        print()
        print(f"  {_C.GRY}Session  : {_C.RESET}{_C.BOLD}{session_id}{_C.RESET}")
        print(f"  {_C.GRY}Customer : {_C.RESET}{_C.BOLD}{customer_id}{_C.RESET}")
        print(f"  {_C.GRY}Type a question and press Enter.  Type 'quit' to exit.{_C.RESET}")
        print()

        # ── Build agents (one line per agent so students see initialisation order)
        print(f"  {_C.GRY}[SYSTEM]  Initializing agent graph...{_C.RESET}")
        inventory_agent     = build_inventory_agent()
        print(f"  {_C.GRY}          {_C.OK}[OK]{_C.RESET}{_C.GRY}  InventoryAgent{_C.RESET}",    flush=True)
        refund_agent        = build_refund_agent()
        print(f"  {_C.GRY}          {_C.OK}[OK]{_C.RESET}{_C.GRY}  RefundAgent{_C.RESET}",       flush=True)
        policy_agent        = build_policy_agent()
        print(f"  {_C.GRY}          {_C.OK}[OK]{_C.RESET}{_C.GRY}  PolicyAgent{_C.RESET}",       flush=True)
        communication_agent = build_communication_agent()
        print(f"  {_C.GRY}          {_C.OK}[OK]{_C.RESET}{_C.GRY}  CommunicationAgent{_C.RESET}", flush=True)
        orchestrator = build_orchestrator_agent(
            inventory_agent, refund_agent, policy_agent, communication_agent
        )
        print(f"  {_C.GRY}          {_C.OK}[OK]{_C.RESET}{_C.GRY}  Orchestrator{_C.RESET}",      flush=True)
        print(f"  {_C.GRY}[SYSTEM]  All 5 agents ready.{_C.RESET}")
        print()

        # ── Conversation loop ─────────────────────────────────────────────
        while True:
            try:
                user_input = input(
                    f"  {_C.BOLD}You >{_C.RESET} "
                ).strip()
            except (EOFError, KeyboardInterrupt):
                print(f"\n  {_C.GRY}Session ended.{_C.RESET}")
                break

            if not user_input:
                continue
            if user_input.lower() in ('quit', 'exit', 'q'):
                print(f"  {_C.GRY}Session ended.{_C.RESET}")
                break

            prompt  = (f"[Session ID: {session_id}] "
                       f"[Customer ID: {customer_id}] {user_input}")
            t0_turn = time.time()

            # ── Install proxy, run orchestrator, restore stdout ────────────
            trace.new_turn()
            sys.stdout = _trace_writer
            try:
                response = orchestrator(prompt)
            finally:
                sys.stdout = _real_stdout   # always restore, even on exception

            elapsed = time.time() - t0_turn

            # ── Resolve the final customer-facing text ────────────────────
            final_state = _read_workflow_state(session_id) or {}
            comm_result = final_state.get('communication_agent', '')
            text = _strip_xml_tags(comm_result or str(response))

            # ── DynamoDB workflow state summary ───────────────────────────
            trace.summary(session_id, elapsed)

            # ── Final customer-facing response ────────────────────────────
            print()
            print(f"  {_C.GRY}{'=' * W}{_C.RESET}")
            print(f"  {_C.COM}{_C.BOLD}AGENT RESPONSE{_C.RESET}")
            print(f"  {_C.GRY}{'=' * W}{_C.RESET}")
            for line in text.splitlines():
                print(f"  {line}")
            print(f"  {_C.GRY}{'=' * W}{_C.RESET}")
            print()

    else:
        print("Usage:")
        print("  python agent_orchestrator.py deploy  # Deploy to AgentCore")
        print("  python agent_orchestrator.py test    # Run automated test cases")
        print("  python agent_orchestrator.py chat    # Interactive terminal chat")
