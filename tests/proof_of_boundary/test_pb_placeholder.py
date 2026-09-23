"""Proof-of-Boundary tests — CMN-C1-122 EnterpriseAPIChangeImpactAgent.

Migrated to SDK pattern: FunctionNode, emit_trace_event, InvocationContext.from_state().
"""

import ast
import json
import pathlib
import re
from unittest.mock import MagicMock, patch


from framework.schemas.invocation_context import InvocationContext, TrustLevel
from framework.secrets.context import bound_secrets
from shared.secrets.inmemory_provider import InMemoryProvider

from src.nodes.answer_generate_node import AnswerGenerateNode
from src.nodes.api_kb_retrieve_node import APIKBRetrieveNode
from src.nodes.impact_analyze_node import ImpactAnalyzeNode
from src.nodes.query_embed_node import QueryEmbedNode

_SECRETS = InMemoryProvider(
    {
        "AZURE_OPENAI_API_KEY": "test-key",
        "AZURE_OPENAI_ENDPOINT": "https://test.openai.azure.com",
        "AZURE_OPENAI_DEPLOYMENT": "test-deployment",
        "QDRANT_URL": "http://localhost:6333",
    }
)

FIXTURE_RECORD_FLAT = {
    "endpoint": "/v1/payments",
    "api_version": "v54",
    "change_type": "DEPRECATION",
    "score": 0.92,
    "consumer_systems": ["checkout", "billing"],
    "owner_team": "payments-team",
    "last_updated": "2026-05-01",
}

LLM_FIXTURE_RESPONSE = json.dumps(
    {
        "summary": "Salesforce API v54 deprecation affects 3 integrations.",
        "max_severity": "CRITICAL",
        "blast_radius": 3,
        "affected_integrations": [
            {
                "name": "checkout",
                "severity": "CRITICAL",
                "reason": "Direct v54 dependency",
                "recommended_action": "Migrate to v56",
            },
            {
                "name": "billing",
                "severity": "HIGH",
                "reason": "Indirect dependency",
                "recommended_action": "Update SDK",
            },
            {"name": "reporting", "severity": "LOW", "reason": "Read-only usage", "recommended_action": "Monitor"},
        ],
        "remediation_steps": ["Audit all v54 usages", "Update SDK to v56", "Run integration tests"],
    }
)


def _ctx():
    return InvocationContext(caller_id="test", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)


def _base_state(**overrides) -> dict:
    state = {
        "user_input": "Which integrations break if Salesforce API v54 is deprecated?",
        "query": "Which integrations break if Salesforce API v54 is deprecated?",
        "query_embedding": json.dumps([0.1] * 1536),
        "query_normalized": "which integrations break if salesforce api v54 is deprecated?",
        "retrieved_api_records": json.dumps([FIXTURE_RECORD_FLAT]),
        "retrieval_count": 1,
        "impact_analysis": LLM_FIXTURE_RESPONSE,
        "affected_integrations": json.dumps(json.loads(LLM_FIXTURE_RESPONSE)["affected_integrations"]),
        "output": None,
        "formatted_output": None,
        "error": None,
        "correlation_id": "test-cid-001",
        "session_id": "test-session",
        "thread_id": "test-thread",
        "trace_id": "",
        "caller_trust_level": "VERIFIED_EXTERNAL",
        "caller_id": "test",
        "hitl_allowed": True,
        "node_history": [],
        "error_log": [],
        "status": "pending",
        "execution_time": {},
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# PB-1: emit_trace_event fires at each node boundary
# ---------------------------------------------------------------------------


class TestPB1EmitTraceEvent:
    """PB-1: Every node must call emit_trace_event at least once."""

    def test_query_embed_node_emits(self):
        node = QueryEmbedNode()
        state = _base_state()
        events = []
        with patch("src.nodes.query_embed_node.emit_trace_event", side_effect=lambda **kw: events.append(kw)):
            with patch.object(node, "_embed", return_value=[0.1] * 1536):
                with bound_secrets(_SECRETS):
                    node.execute(state)
        assert len(events) >= 1

    def test_api_kb_retrieve_node_emits(self):
        node = APIKBRetrieveNode()
        state = _base_state()
        events = []
        with patch("src.nodes.api_kb_retrieve_node.emit_trace_event", side_effect=lambda **kw: events.append(kw)):
            with patch("src.nodes.api_kb_retrieve_node.QdrantClient") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.search.return_value = []
                mock_client_cls.return_value = mock_client
                with bound_secrets(_SECRETS):
                    node.execute(state)
        assert len(events) >= 1

    def test_impact_analyze_node_emits(self):
        node = ImpactAnalyzeNode()
        mock_llm = MagicMock()
        mock_llm.complete.return_value = {"content": LLM_FIXTURE_RESPONSE}
        node._build_llm = lambda state: mock_llm
        state = _base_state()
        events = []
        with patch("src.nodes.impact_analyze_node.emit_trace_event", side_effect=lambda **kw: events.append(kw)):
            with bound_secrets(_SECRETS):
                node.execute(state)
        assert len(events) >= 1

    def test_answer_generate_node_emits(self):
        node = AnswerGenerateNode()
        state = _base_state()
        events = []
        with patch("src.nodes.answer_generate_node.emit_trace_event", side_effect=lambda **kw: events.append(kw)):
            with bound_secrets(_SECRETS):
                node.execute(state)
        assert len(events) >= 1


# ---------------------------------------------------------------------------
# PB-2: State serialization (msgpack safety)
# ---------------------------------------------------------------------------


class TestPB2StateMsgpackSafe:
    def test_answer_generate_output_is_primitive(self):
        node = AnswerGenerateNode()
        state = _base_state()
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        for key, value in result.items():
            assert value is None or isinstance(
                value, (str, int, float, bool, dict, list)
            ), f"result['{key}'] = {type(value).__name__} — not safe"


# ---------------------------------------------------------------------------
# PB-3: L1 framework → Qdrant boundary
# ---------------------------------------------------------------------------


class TestPB3QdrantBoundary:
    def test_no_direct_qdrant_client_import_at_module_level(self):
        src_file = pathlib.Path("src/nodes/api_kb_retrieve_node.py")
        source = src_file.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "qdrant_client", "Direct qdrant_client top-level import"


# ---------------------------------------------------------------------------
# PB-4: Import isolation (no L0)
# ---------------------------------------------------------------------------


class TestPB4ImportIsolation:
    def test_no_l0_imports_in_src(self):
        violations = []
        for path in pathlib.Path("src").rglob("*.py"):
            source = path.read_text()
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("agenticstar"):
                            violations.append(f"{path}: import {alias.name}")
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.startswith("agenticstar") or node.module.startswith("agents.base"):
                        violations.append(f"{path}: from {node.module}")
        assert violations == [], "Import isolation violations:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# PB-5: Checkpoint safety
# ---------------------------------------------------------------------------


class TestPB5CheckpointSafety:
    _CREDENTIAL_PATTERNS = [
        r"eyJ[A-Za-z0-9._-]+",
        r"sk-[a-zA-Z0-9]{20,}",
        r"Bearer [a-zA-Z0-9._-]{20,}",
        r"AKIA[A-Z0-9]{16}",
    ]

    def test_no_credential_in_answer_generate_result(self):
        node = AnswerGenerateNode()
        state = _base_state()
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        serialized = json.dumps(result, default=str)
        for pattern in self._CREDENTIAL_PATTERNS:
            assert not re.search(pattern, serialized), f"Credential pattern {pattern!r} in result"


# ---------------------------------------------------------------------------
# PB-6: Node execution contract
# ---------------------------------------------------------------------------


class TestPB6NodeContract:
    def test_nodes_have_required_trust_level(self):
        assert QueryEmbedNode.required_trust_level == TrustLevel.VERIFIED_EXTERNAL
        assert APIKBRetrieveNode.required_trust_level == TrustLevel.ANONYMOUS
        assert ImpactAnalyzeNode.required_trust_level == TrustLevel.ANONYMOUS
        assert AnswerGenerateNode.required_trust_level == TrustLevel.ANONYMOUS

    def test_s2_fires_before_embed(self):
        node = QueryEmbedNode()
        call_log = []
        state = _base_state(user_input="sk-" + "a" * 25)

        def _spy_embed(*a, **kw):
            call_log.append("EMBED")
            return [0.0] * 1536

        with patch("src.nodes.query_embed_node.emit_trace_event"):
            with patch.object(node, "_embed", side_effect=_spy_embed):
                with bound_secrets(_SECRETS):
                    result = node.execute(state)
        assert "EMBED" not in call_log, "embed must not be called when S-2 gate blocks"
        assert result.get("error") is not None

    def test_s3_prevents_output_write(self):
        node = AnswerGenerateNode()
        state = _base_state()
        with patch.object(node, "_run_output_gate", return_value={"error": "S3 blocked", "status": "error"}):
            with bound_secrets(_SECRETS):
                result = node.execute(state)
        assert result.get("output") is None
