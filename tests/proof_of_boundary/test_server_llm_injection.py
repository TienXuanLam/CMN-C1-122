"""Standalone adapter dependency injection and trust-boundary checks."""

import importlib


def test_server_boots_without_openai_key(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)
    monkeypatch.delenv("STG_MOCK_MODE", raising=False)

    import src.api.server as server

    importlib.reload(server)
    assert server.app is not None
    assert server.agent is not None


def test_stg_mock_dependencies_reach_graph(monkeypatch):
    monkeypatch.setenv("STG_MOCK_MODE", "true")

    import src.api.server as server

    importlib.reload(server)
    assert isinstance(server.agent._nodes["pre_process"]._embedding_client, server._MockEmbedding)
    assert isinstance(server.agent._nodes["main"]._retriever._qdrant_client, server._MockQdrant)


def test_stg_mock_llm_returns_deterministic_response(monkeypatch):
    # LLM mocking is controlled by STG_MOCK_MODE at ImpactAnalyzeNode itself
    # (see its _build_llm() docstring), read independently from server.py --
    # the node builds a fresh, secret-bound client per invocation and has no
    # constructor-injected client for server.py to reach into.
    monkeypatch.setenv("STG_MOCK_MODE", "true")

    from src.nodes.impact_analyze_node import ImpactAnalyzeNode, _MockLLM

    node = ImpactAnalyzeNode()
    llm = node._build_llm({})
    assert isinstance(llm, _MockLLM)


def test_standalone_trust_tokens_have_distinct_levels():
    import src.api.server as server
    from framework.schemas.trust_level import TrustLevel

    assert (
        server._resolve_standalone_trust(TrustLevel.ANONYMOUS, "Bearer external", "external", "runner")
        is TrustLevel.VERIFIED_EXTERNAL
    )
    assert (
        server._resolve_standalone_trust(TrustLevel.ANONYMOUS, "Bearer runner", "external", "runner")
        is TrustLevel.INTERNAL
    )
