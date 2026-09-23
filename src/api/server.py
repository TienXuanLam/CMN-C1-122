"""Standalone FastAPI adapter for CMN-C1-122."""

import logging
import os
import secrets
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from framework.utils.config_loader import load_config
from shared.secrets import factory as secrets_factory
from src.graph.graph import APIChangeImpactGraph

logger = logging.getLogger(__name__)


class _MockEmbedding:
    def embed_query(self, _text: str) -> list[float]:
        return [0.0] * 1536


class _MockQdrant:
    def search(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "score": 0.99,
                "payload": {
                    "endpoint": "/v1/customers",
                    "api_version": "v1",
                    "change_type": "DEPRECATION",
                    "consumer_systems": ["customer-portal"],
                    "owner_team": "platform-api",
                },
            }
        ]


_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
_config = load_config(str(_CONFIG_PATH)) if _CONFIG_PATH.exists() else {}
_secrets_provider = secrets_factory(namespace="cmn", agent_name="cmn-c1-122")

# LLM construction is no longer wired here: ImpactAnalyzeNode builds its own
# secret-bound AzureOpenAIClient per invocation via _build_llm(state) (see
# its docstring) -- config["llm"] is never read by the graph. STG_MOCK_MODE
# still injects Qdrant/embedding via constructor (those nodes still support
# it); LLM mocking is also controlled by STG_MOCK_MODE, read independently
# at ImpactAnalyzeNode itself, not here.
if os.environ.get("STG_MOCK_MODE", "").lower() == "true":
    _config.update({"embedding": _MockEmbedding(), "qdrant": _MockQdrant()})

# No Azure embedding-capable deployment is provisioned yet (the current
# AZURE_OPENAI_DEPLOYMENT is a chat model; embeddings require a separate
# text-embedding-* deployment). Until one exists, `embedding` is left unset
# outside STG_MOCK_MODE, so QueryEmbedNode falls through to its own
# AzureEmbeddingService fallback (see query_embed_node.py).

agent = APIChangeImpactGraph(config=_config)
_hitl = agent.config.get("hitl", {})
_hitl_enabled = _hitl.get("enabled", False) if isinstance(_hitl, dict) else False
_needs_checkpointer = bool(agent.config.get("memory_enabled") or _hitl_enabled)
agent.compile(checkpointer=MemorySaver() if _needs_checkpointer else None)
agent.provision_secrets(_secrets_provider)

app = FastAPI(title="EnterpriseAPIChangeImpactAgent")


class InvokeRequest(BaseModel):
    input: str
    session_id: str = ""


def _bearer_matches(supplied: str, expected: str) -> bool:
    return secrets.compare_digest(supplied.encode(), f"Bearer {expected}".encode())


def _resolve_standalone_trust(
    current: TrustLevel,
    authorization: str,
    invoke_auth_token: str | None,
    internal_runner_token: str | None,
) -> TrustLevel:
    if current is not TrustLevel.ANONYMOUS:
        return current
    if internal_runner_token and _bearer_matches(authorization, internal_runner_token):
        return TrustLevel.INTERNAL
    if invoke_auth_token and _bearer_matches(authorization, invoke_auth_token):
        return TrustLevel.VERIFIED_EXTERNAL
    if internal_runner_token or invoke_auth_token:
        raise HTTPException(status_code=401, detail="Token is invalid or expired.")
    return TrustLevel.ANONYMOUS


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request) -> dict[str, Any]:
    trust = _resolve_standalone_trust(
        getattr(request.state, "trust_level", TrustLevel.ANONYMOUS),
        request.headers.get("authorization", ""),
        os.environ.get("INVOKE_AUTH_TOKEN"),
        os.environ.get("STG_INTERNAL_RUNNER_TOKEN"),
    )
    with bound_secrets(agent._secrets_provider):
        ctx = InvocationContext(
            session_id=req.session_id or str(uuid4()),
            caller_trust_level=trust,
            caller_id=getattr(request.state, "caller_id", ""),
        )
        return cast(
            "dict[str, Any]",
            agent.invoke(
                user_input=req.input,
                ctx=ctx,
            ),
        )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "agent": "cmn-c1-122"}
