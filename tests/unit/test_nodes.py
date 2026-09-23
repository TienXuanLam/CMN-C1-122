"""Unit tests — CMN-C1-122 EnterpriseAPIChangeImpactAgent (SDK pattern)."""

import json
from unittest.mock import MagicMock, patch


from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
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

_EMBEDDING = [0.1] * 1536

_FLAT_RECORD = {
    "endpoint": "/v1/payments",
    "api_version": "v54",
    "change_type": "DEPRECATION",
    "score": 0.92,
    "consumer_systems": ["checkout", "billing"],
    "owner_team": "payments-team",
    "last_updated": "2026-05-01",
}

_LLM_FIXTURE = {
    "summary": "API v54 deprecation affects 3 integrations.",
    "max_severity": "CRITICAL",
    "blast_radius": 3,
    "affected_integrations": [
        {"name": "checkout", "severity": "CRITICAL", "reason": "Direct dependency", "recommended_action": "Migrate"},
        {"name": "billing", "severity": "HIGH", "reason": "Indirect", "recommended_action": "Update SDK"},
        {"name": "reporting", "severity": "LOW", "reason": "Read-only", "recommended_action": "Monitor"},
    ],
    "remediation_steps": ["Audit v54 usages", "Update SDK", "Run tests"],
}
_LLM_FIXTURE_JSON = json.dumps(_LLM_FIXTURE)


def _state(**overrides) -> dict:
    base = {
        "user_input": "Which integrations break if Salesforce API v54 is deprecated?",
        "query": "Which integrations break if Salesforce API v54 is deprecated?",
        "query_embedding": json.dumps(_EMBEDDING),
        "query_normalized": "which integrations break?",
        "retrieved_api_records": json.dumps([_FLAT_RECORD]),
        "retrieval_count": 1,
        "impact_analysis": _LLM_FIXTURE_JSON,
        "affected_integrations": json.dumps(_LLM_FIXTURE["affected_integrations"]),
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


# ---------------------------------------------------------------------------
# QueryEmbedNode
# ---------------------------------------------------------------------------


class TestQueryEmbedNode:
    def test_text_passthrough_and_embedding(self):
        node = QueryEmbedNode()
        state = _state(query_embedding=None)
        with patch.object(node, "_embed", return_value=_EMBEDDING):
            with bound_secrets(_SECRETS):
                result = node.execute(state)
        assert result["query_embedding"] == json.dumps(_EMBEDDING)
        assert result["query_normalized"] == state["user_input"].strip().lower()

    def test_credential_in_query_blocked(self):
        node = QueryEmbedNode()
        state = _state(user_input="sk-" + "a" * 25)
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        assert result.get("error") is not None
        assert result.get("status") == AgentStatus.ERROR.value

    def test_empty_query_blocked(self):
        node = QueryEmbedNode()
        state = _state(user_input="")
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        assert result.get("error") is not None

    def test_upstream_error_propagates(self):
        node = QueryEmbedNode()
        state = _state(error="upstream error")
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        assert result == {}

    def test_required_trust_level(self):
        assert QueryEmbedNode.required_trust_level == TrustLevel.VERIFIED_EXTERNAL

    def test_execute_contract(self):
        import inspect

        sig = inspect.signature(QueryEmbedNode.execute)
        assert "state" in sig.parameters
        assert "_invoke_impl" not in QueryEmbedNode.__dict__


# ---------------------------------------------------------------------------
# APIKBRetrieveNode
# ---------------------------------------------------------------------------


class TestAPIKBRetrieveNode:
    def test_happy_path_returns_records(self):
        node = APIKBRetrieveNode()
        state = _state()
        qdrant_record = {"score": 0.92, "payload": {**_FLAT_RECORD}}
        with patch("src.nodes.api_kb_retrieve_node.QdrantClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.search.return_value = [qdrant_record]
            mock_cls.return_value = mock_client
            with bound_secrets(_SECRETS):
                result = node.execute(state)
        assert result["retrieval_count"] == 1
        assert len(json.loads(result["retrieved_api_records"])) == 1

    def test_missing_query_embedding_returns_error(self):
        node = APIKBRetrieveNode()
        state = _state(query_embedding=None)
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        assert result.get("error") is not None

    def test_upstream_error_propagates(self):
        node = APIKBRetrieveNode()
        state = _state(error="upstream")
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        assert result == {}

    def test_required_trust_level(self):
        assert APIKBRetrieveNode.required_trust_level == TrustLevel.ANONYMOUS

    def test_missing_qdrant_url_falls_back_to_fixture(self):
        # TEMPORARY fallback (see api_kb_retrieve_node.py) -- without
        # QDRANT_URL, the node must not error; it returns the hardcoded
        # fixture KB instead of failing every real Marketplace invocation.
        node = APIKBRetrieveNode()
        state = _state()
        no_qdrant_secrets = InMemoryProvider({"AZURE_OPENAI_API_KEY": "test-key"})
        with bound_secrets(no_qdrant_secrets):
            result = node.execute(state)
        assert result.get("error") is None
        assert result["retrieval_count"] >= 1


# ---------------------------------------------------------------------------
# ImpactAnalyzeNode
# ---------------------------------------------------------------------------


def _impact_node(llm=None) -> ImpactAnalyzeNode:
    node = ImpactAnalyzeNode()
    node._build_llm = lambda state: llm if llm is not None else MagicMock()
    return node


class TestImpactAnalyzeNode:
    def test_happy_path(self):
        mock_llm = MagicMock()
        mock_llm.complete.return_value = {"content": _LLM_FIXTURE_JSON}
        node = _impact_node(mock_llm)
        state = _state()
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        assert json.loads(result["impact_analysis"])["max_severity"] == "CRITICAL"
        assert len(json.loads(result["affected_integrations"])) == 3

    def test_empty_records_returns_empty(self):
        mock_llm = MagicMock()
        node = _impact_node(mock_llm)
        state = _state(retrieved_api_records="[]")
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        assert result == {}
        mock_llm.complete.assert_not_called()

    def test_llm_build_failure_returns_error(self):
        # _build_llm() reads secrets via ctx.secrets.require() -- missing
        # secrets surface as an exception here, caught by execute()'s
        # try/except around the LLM call (replaces the old "no llm_client
        # injected" case, which no longer exists now that _build_llm()
        # always returns a client rather than a possibly-None constructor arg).
        node = ImpactAnalyzeNode()

        def _raise(_state: dict) -> None:
            raise RuntimeError("secret not bound")

        node._build_llm = _raise
        state = _state()
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        assert result.get("error") is not None

    def test_malformed_llm_json_returns_error(self):
        mock_llm = MagicMock()
        mock_llm.complete.return_value = {"content": "not json"}
        node = _impact_node(mock_llm)
        state = _state()
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        assert result.get("error") is not None

    def test_blast_radius_corrected(self):
        wrong = {**_LLM_FIXTURE, "blast_radius": 99}
        mock_llm = MagicMock()
        mock_llm.complete.return_value = {"content": json.dumps(wrong)}
        node = _impact_node(mock_llm)
        state = _state()
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        impact = json.loads(result["impact_analysis"])
        assert impact["blast_radius"] == 3

    def test_upstream_error_propagates(self):
        node = _impact_node()
        state = _state(error="upstream")
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        assert result == {}


# ---------------------------------------------------------------------------
# AnswerGenerateNode
# ---------------------------------------------------------------------------


class TestAnswerGenerateNode:
    def test_happy_path_contains_sections(self):
        node = AnswerGenerateNode()
        state = _state()
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        assert "## API Change Impact Report" in result["output"]
        assert "### Summary" in result["output"]
        assert "### Affected Integrations" in result["output"]
        assert "### Remediation Steps" in result["output"]
        assert result["formatted_output"] == result["output"]

    def test_empty_retrieval_returns_no_match(self):
        node = AnswerGenerateNode()
        state = _state(retrieval_count=0)
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        assert "No matching API records found" in result["output"]

    def test_missing_impact_analysis_returns_error(self):
        node = AnswerGenerateNode()
        state = _state(impact_analysis=None)
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        assert result.get("error") is not None

    def test_s3_gate_blocks_credential(self):
        node = AnswerGenerateNode()
        state = _state()
        with patch.object(node, "_run_output_gate", return_value={"error": "S3", "status": "error"}):
            with bound_secrets(_SECRETS):
                result = node.execute(state)
        assert result.get("output") is None

    def test_pipe_escaped_in_table_cell(self):
        node = AnswerGenerateNode()
        integrations = [{**_LLM_FIXTURE["affected_integrations"][0], "reason": "foo|bar"}]
        impact = {**_LLM_FIXTURE, "affected_integrations": integrations, "blast_radius": 1}
        state = _state(impact_analysis=json.dumps(impact), affected_integrations=json.dumps(integrations))
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        assert "foo\\|bar" in result["output"]

    def test_upstream_error_propagates(self):
        node = AnswerGenerateNode()
        state = _state(error="upstream")
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        assert result == {}

    def test_required_trust_level(self):
        assert AnswerGenerateNode.required_trust_level == TrustLevel.ANONYMOUS
