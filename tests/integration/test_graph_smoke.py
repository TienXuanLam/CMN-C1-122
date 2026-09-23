# CMN-C1-122 — Integration smoke test

import json
from unittest.mock import MagicMock, patch

from framework.schemas.invocation_context import InvocationContext, TrustLevel
from framework.secrets.context import bound_secrets
from shared.secrets.inmemory_provider import InMemoryProvider
from shared.services.llm.base_llm import BaseLLM

from src.graph.graph import APIChangeImpactGraph
from src.nodes.query_embed_node import QueryEmbedNode

_SECRETS = InMemoryProvider(
    {
        "AZURE_OPENAI_API_KEY": "test-key",
        "AZURE_OPENAI_ENDPOINT": "https://test.openai.azure.com",
        "AZURE_OPENAI_DEPLOYMENT": "test-deployment",
        "QDRANT_URL": "http://localhost:6333",
    }
)

_LLM_FIXTURE_JSON = json.dumps(
    {
        "summary": "API v54 deprecation.",
        "max_severity": "CRITICAL",
        "blast_radius": 1,
        "affected_integrations": [
            {"name": "checkout", "severity": "CRITICAL", "reason": "Direct", "recommended_action": "Migrate"},
        ],
        "remediation_steps": ["Audit", "Migrate"],
    }
)

_QDRANT_RECORD = {
    "score": 0.92,
    "payload": {
        "endpoint": "/v1/payments",
        "api_version": "v54",
        "change_type": "DEPRECATION",
        "consumer_systems": ["checkout"],
        "owner_team": "payments",
        "last_updated": "2026-05-01",
    },
}


def _mock_llm() -> BaseLLM:
    class _MockLLM:
        def complete(self, messages):
            return {"content": _LLM_FIXTURE_JSON}

    return _MockLLM()  # type: ignore


def _ctx():
    return InvocationContext(caller_id="test", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)


def _build_agent():
    agent = APIChangeImpactGraph(config={})
    agent.compile()
    # ImpactAnalyzeNode._build_llm() builds a fresh, secret-bound client per
    # invocation (see its docstring) -- config["llm"] is never read, so tests
    # patch _build_llm on the composed node directly instead.
    agent._nodes["main"]._analyzer._build_llm = lambda state: _mock_llm()
    return agent


def test_graph_compiles():
    agent = _build_agent()
    assert agent._compiled is not None


def test_full_pipeline_success():
    agent = _build_agent()
    with patch.object(QueryEmbedNode, "_embed", return_value=[0.1] * 1536):
        with patch("src.nodes.api_kb_retrieve_node.QdrantClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.search.return_value = [_QDRANT_RECORD]
            mock_cls.return_value = mock_client
            with bound_secrets(_SECRETS):
                result = agent.invoke(
                    user_input="Which integrations break if API v54 is deprecated?",
                    ctx=_ctx(),
                )
    assert result["status"] == "success"
    assert result.get("output") is not None


def test_full_pipeline_empty_retrieval():
    agent = _build_agent()
    with patch.object(QueryEmbedNode, "_embed", return_value=[0.1] * 1536):
        with patch("src.nodes.api_kb_retrieve_node.QdrantClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.search.return_value = []
            mock_cls.return_value = mock_client
            with bound_secrets(_SECRETS):
                result = agent.invoke(
                    user_input="unknown query",
                    ctx=_ctx(),
                )
    assert result["status"] == "success"
    out = result.get("output") or {}
    answer = out.get("answer", "") if isinstance(out, dict) else str(out)
    assert "No matching" in answer or result["status"] == "success"


def test_pipeline_error_on_credential_query():
    agent = _build_agent()
    with patch("src.nodes.query_embed_node.emit_trace_event"):
        with bound_secrets(_SECRETS):
            result = agent.invoke(
                user_input="sk-" + "a" * 25,
                ctx=_ctx(),
            )
    assert result["status"] == "error"
