# CMN-C1-122 — Unit Tests: MainNode contract

import inspect
import json
from unittest.mock import MagicMock, patch


from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from shared.secrets.inmemory_provider import InMemoryProvider

from src.nodes.main_node import MainNode

_SECRETS = InMemoryProvider(
    {
        "AZURE_OPENAI_API_KEY": "test-key",
        "AZURE_OPENAI_ENDPOINT": "https://test.openai.azure.com",
        "AZURE_OPENAI_DEPLOYMENT": "test-deployment",
        "QDRANT_URL": "http://localhost:6333",
    }
)

_EMBEDDING = [0.1] * 1536

_FLAT_RECORD = {
    "endpoint": "/v1/payments",
    "api_version": "v54",
    "change_type": "DEPRECATION",
    "score": 0.92,
    "consumer_systems": ["checkout"],
    "owner_team": "payments-team",
    "last_updated": "2026-05-01",
}

_LLM_FIXTURE = {
    "summary": "API v54 deprecation.",
    "max_severity": "CRITICAL",
    "blast_radius": 1,
    "affected_integrations": [
        {"name": "checkout", "severity": "CRITICAL", "reason": "Direct", "recommended_action": "Migrate"},
    ],
    "remediation_steps": ["Audit", "Migrate"],
}


def _state(**overrides) -> dict:
    base = {
        "user_input": "Which integrations break if Salesforce API v54 is deprecated?",
        "query": "Which integrations break if Salesforce API v54 is deprecated?",
        "query_embedding": json.dumps(_EMBEDDING),
        "query_normalized": "which integrations break?",
        "retrieved_api_records": None,
        "retrieval_count": None,
        "impact_analysis": None,
        "affected_integrations": None,
        "output": None,
        "formatted_output": None,
        "error": None,
        "correlation_id": "test-cid",
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
    base.update(overrides)
    return base


class TestMainNodeContract:
    def test_execute_signature(self):
        node = MainNode()
        sig = inspect.signature(node.execute)
        assert "state" in sig.parameters

    def test_no_invoke_impl_override(self):
        assert "_invoke_impl" not in MainNode.__dict__

    def test_required_trust_level(self):
        assert MainNode.required_trust_level == TrustLevel.VERIFIED_EXTERNAL

    def test_is_function_node(self):
        from framework.nodes.function_node import FunctionNode

        assert issubclass(MainNode, FunctionNode)


class TestMainNodeHappyPath:
    def _mock_llm(self) -> MagicMock:
        llm = MagicMock()
        llm.complete.return_value = {"content": json.dumps(_LLM_FIXTURE)}
        return llm

    def _node_with_llm(self, mock_llm: MagicMock) -> MainNode:
        node = MainNode()
        # ImpactAnalyzeNode._build_llm() builds a fresh, secret-bound client
        # per invocation (see its docstring) -- there is no constructor
        # injection seam anymore, so tests patch _build_llm directly instead.
        node._analyzer._build_llm = lambda state: mock_llm
        return node

    def test_full_pipeline_returns_retrieval_and_impact(self):
        mock_llm = self._mock_llm()
        node = self._node_with_llm(mock_llm)
        state = _state()
        qdrant_record = {"score": 0.92, "payload": {**_FLAT_RECORD}}
        with patch("src.nodes.api_kb_retrieve_node.QdrantClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.search.return_value = [qdrant_record]
            mock_cls.return_value = mock_client
            with bound_secrets(_SECRETS):
                result = node.execute(state)
        assert result.get("retrieval_count") == 1
        assert result.get("impact_analysis") is not None
        impact = json.loads(result["impact_analysis"])
        assert impact["max_severity"] == "CRITICAL"

    def test_empty_retrieval_skips_analysis(self):
        mock_llm = self._mock_llm()
        node = self._node_with_llm(mock_llm)
        state = _state()
        with patch("src.nodes.api_kb_retrieve_node.QdrantClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.search.return_value = []
            mock_cls.return_value = mock_client
            with bound_secrets(_SECRETS):
                result = node.execute(state)
        assert result.get("retrieval_count") == 0
        mock_llm.complete.assert_not_called()

    def test_sets_success_status(self):
        mock_llm = self._mock_llm()
        node = self._node_with_llm(mock_llm)
        state = _state()
        with patch("src.nodes.api_kb_retrieve_node.QdrantClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.search.return_value = [{"score": 0.92, "payload": _FLAT_RECORD}]
            mock_cls.return_value = mock_client
            with bound_secrets(_SECRETS):
                result = node.execute(state)
        assert result.get("status") == AgentStatus.SUCCESS.value


class TestMainNodeErrorHandling:
    def test_upstream_error_returns_empty(self):
        node = MainNode()
        state = _state(error="upstream error")
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        assert result == {}

    def test_retriever_error_does_not_invoke_analyzer(self):
        mock_llm = MagicMock()
        node = MainNode()
        node._analyzer._build_llm = lambda state: mock_llm
        state = _state(query_embedding=None)
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        assert result.get("error") is not None
        mock_llm.complete.assert_not_called()
