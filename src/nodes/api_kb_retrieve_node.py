"""AgentCore Platform v1.0"""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Credentials via ctx.secrets — never from state or os.environ
#  - Never import from mediator/, api/, or other agents

import json
from typing import Any, Optional

try:
    from qdrant_client import QdrantClient
except ImportError:  # qdrant_client is an optional extra; absent in unit-test CI
    QdrantClient = None  # type: ignore[assignment,misc]

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import APIChangeImpactState

_HARD_REQUIRED = ("endpoint", "api_version", "change_type", "score")

# TEMPORARY fallback (2026-09-11): no Qdrant instance is provisioned for this
# agent yet -- ctx.secrets.require("QDRANT_URL") always raises MissingSecret
# on the real Marketplace path, so the agent could never answer any query.
# Returns this hardcoded, illustrative KB in place of a real vector search
# until a Qdrant instance + real index exist. NOT real data -- remove this
# fallback (and _FIXTURE_RECORDS) once QDRANT_URL is provisioned; see
# docs/06_release_note.md.
_FIXTURE_RECORDS: list[dict[str, Any]] = [
    {
        "endpoint": "/v1/customers",
        "api_version": "v1",
        "change_type": "DEPRECATION",
        "score": 0.99,
        "consumer_systems": ["customer-portal", "billing-service"],
        "owner_team": "platform-api",
        "last_updated": "2026-06-01",
    },
    {
        "endpoint": "/v1/payments",
        "api_version": "v1",
        "change_type": "BREAKING",
        "score": 0.97,
        "consumer_systems": ["checkout-service"],
        "owner_team": "payments-team",
        "last_updated": "2026-05-15",
    },
]


class APIKBRetrieveNode(FunctionNode):
    """Vector similarity search over API inventory KB (Qdrant)."""

    required_trust_level = TrustLevel.ANONYMOUS

    def __init__(
        self,
        score_threshold: float = 0.75,
        top_k: int = 5,
        collection: str = "api_inventory",
        qdrant_client: Optional[Any] = None,
    ):
        self._score_threshold = score_threshold
        self._top_k = top_k
        self._collection = collection
        self._qdrant_client = qdrant_client

    def execute(self, state: APIChangeImpactState) -> dict[str, Any]:
        # Propagate upstream error before emitting trace
        if state.get("error"):
            return {}

        ctx = InvocationContext.from_state(state)

        emit_trace_event(
            event_type="kb_retrieve_start",
            payload={"collection": self._collection, "top_k": self._top_k},
            state=state,
        )

        raw_embedding = state.get("query_embedding")
        if not raw_embedding:
            return {
                "error": "APIKBRetrieveNode: query_embedding missing — QueryEmbedNode must run first",
                "status": AgentStatus.ERROR.value,
            }

        if QdrantClient is None:
            return {
                "error": "APIKBRetrieveNode: qdrant_client package not installed",
                "status": AgentStatus.ERROR.value,
            }

        query_embedding: list[Any] = json.loads(raw_embedding)
        if self._qdrant_client is None and not ctx.secrets.get("QDRANT_URL"):
            # TEMPORARY: see _FIXTURE_RECORDS above.
            emit_trace_event(
                event_type="kb_retrieve_fixture_fallback",
                payload={"reason": "QDRANT_URL not provisioned"},
                state=state,
            )
            results = [{"score": r["score"], "payload": r} for r in _FIXTURE_RECORDS]
        else:
            try:
                if self._qdrant_client is not None:
                    client = self._qdrant_client
                else:
                    qdrant_url = ctx.secrets.require("QDRANT_URL")
                    client = QdrantClient(url=qdrant_url)
                results = client.search(
                    collection_name=self._collection,
                    query_vector=query_embedding,
                    limit=self._top_k,
                )
            except Exception as exc:  # noqa: BLE001
                return {
                    "error": f"APIKBRetrieveNode: KB query failed — {type(exc).__name__}",
                    "status": AgentStatus.ERROR.value,
                }

        records = []
        for record in results:
            # ScoredPoint (Pydantic) exposes .score/.payload; plain dict mocks use ["score"/"payload"]
            score = getattr(record, "score", record.get("score", 0.0))
            if score < self._score_threshold:
                continue
            raw_payload = getattr(record, "payload", None) or record.get("payload", record)
            payload = dict(raw_payload)
            payload["score"] = score
            for required in _HARD_REQUIRED:
                if payload.get(required) is None:
                    return {
                        "error": f"APIKBRetrieveNode: record missing required field '{required}'",
                        "status": AgentStatus.ERROR.value,
                    }
            payload.setdefault("consumer_systems", [])
            payload.setdefault("owner_team", "unknown")
            payload.setdefault("last_updated", None)
            records.append(payload)

        retrieval_count = len(records)

        emit_trace_event(
            event_type="kb_retrieve_complete",
            payload={"count": retrieval_count, "threshold": self._score_threshold},
            state=state,
        )

        return {
            "retrieved_api_records": json.dumps(records),
            "retrieval_count": retrieval_count,
        }
