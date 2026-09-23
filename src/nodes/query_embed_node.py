"""AgentCore Platform v1.0"""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - S-2: sanitize and gate input before embedding
#  - Never import from mediator/, api/, or other agents

import json
import re
from typing import Any, Optional

from framework.nodes.function_node import FunctionNode
from framework.security import detect_credentials
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import APIChangeImpactState
from src.services.azure_embedding_service import AzureEmbeddingService


class QueryEmbedNode(FunctionNode):
    """S-2 input gate, query sanitisation, and embedding."""

    required_trust_level = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, embedding_model: str = "text-embedding-3-small", embedding_client: Optional[Any] = None):
        self._embedding_client = embedding_client
        self._embedding_service = AzureEmbeddingService(model=embedding_model)

    def execute(self, state: APIChangeImpactState) -> dict[str, Any]:
        if state.get("error"):
            return {}

        # Marketplace and the standalone HTTP adapter both populate only
        # `user_input` -- that is the single real data-carrying channel.
        query = state.get("user_input", "")

        emit_trace_event(
            event_type="query_embed_start",
            payload={"query_len": len(query)},
            state=state,
        )

        # S-2: gate before calling embed
        gate_error = self._run_input_gates(query)
        if gate_error is not None:
            return gate_error

        sanitized = self._sanitize(query)
        embedding = self._embed(sanitized, state)

        emit_trace_event(
            event_type="query_embed_complete",
            payload={"dim": len(embedding)},
            state=state,
        )

        return {
            "query": query,
            "query_embedding": json.dumps(embedding),
            "query_normalized": sanitized.strip().lower(),
        }

    def _run_input_gates(self, query: str) -> Optional[dict[str, Any]]:
        if not query or not query.strip():
            return {
                "error": "QueryEmbedNode: query is empty",
                "status": AgentStatus.ERROR.value,
            }
        if detect_credentials(query):
            return {
                "error": "S-2 violation: credential pattern detected in query",
                "status": AgentStatus.ERROR.value,
            }
        return None

    def _sanitize(self, text: str) -> str:
        return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text).strip()

    def _embed(self, text: str, state: dict[str, Any]) -> list[Any]:
        if self._embedding_client is not None:
            return list(self._embedding_client.embed_query(text))
        # SDK's plain shared.services.embedding.openai_embedding.OpenAIEmbedding
        # is a stub in this wheel (embed_query() unconditionally raises
        # NotImplementedError, verified live against 1.0.3) -- it can never
        # embed a query regardless of API key. AzureEmbeddingService builds
        # the SDK's working Azure-native adapter instead (mirrors
        # AzureOpenAIService.create_client() elsewhere in this fleet).
        embedder = self._embedding_service.create_client(state)
        try:
            return list(embedder.embed_query(text))
        finally:
            self._embedding_service.close_client(embedder)
