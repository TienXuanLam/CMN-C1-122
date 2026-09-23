"""AgentCore Platform v1.0"""

# Composite main slot node: APIKBRetrieveNode → ImpactAnalyzeNode sequentially.
# QueryEmbedNode is pre_process; AnswerGenerateNode is post_process.

from typing import Any, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.nodes.api_kb_retrieve_node import APIKBRetrieveNode
from src.nodes.impact_analyze_node import ImpactAnalyzeNode
from src.schemas.state import APIChangeImpactState


class MainNode(FunctionNode):
    """Compose APIKBRetrieveNode → ImpactAnalyzeNode in the main SDK slot."""

    required_trust_level = TrustLevel.VERIFIED_EXTERNAL

    def __init__(
        self,
        score_threshold: float = 0.75,
        top_k: int = 5,
        collection: str = "api_inventory",
        llm_temperature: float = 0.2,
        llm_max_tokens: int = 4096,
        timeout_s: int = 120,
        max_retry: int = 2,
        qdrant_client: Optional[Any] = None,
    ):
        self._retriever = APIKBRetrieveNode(
            score_threshold=score_threshold,
            top_k=top_k,
            collection=collection,
            qdrant_client=qdrant_client,
        )
        self._analyzer = ImpactAnalyzeNode(
            llm_temperature=llm_temperature,
            llm_max_tokens=llm_max_tokens,
            timeout_s=timeout_s,
            max_retry=max_retry,
        )

    def execute(self, state: APIChangeImpactState) -> dict[str, Any]:
        emit_trace_event(
            event_type="api_change_pipeline_started",
            payload={},
            state=state,
        )
        if state.get("error"):
            return {}

        accumulated: dict[str, Any] = {}

        result = self._retriever({**state, **accumulated})
        accumulated.update(result)
        if accumulated.get("error"):
            return accumulated

        result = self._analyzer({**state, **accumulated})
        accumulated.update(result)

        if "status" not in accumulated:
            accumulated["status"] = AgentStatus.SUCCESS.value

        return accumulated
