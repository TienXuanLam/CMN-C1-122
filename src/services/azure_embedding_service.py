"""Azure OpenAI embedding client construction for CMN-C1-122."""

import asyncio
from typing import Any

from framework.schemas.invocation_context import InvocationContext
from shared.services.embedding.azure_embedding import AzureEmbedding
from shared.services.embedding.base_embedding import BaseEmbedding
from shared.services.runtime import AsyncServiceRuntime


class AzureEmbeddingService:
    """Build the AgentCore Azure embedding client from invocation-scoped secrets.

    AzureEmbedding requires its own AsyncServiceRuntime (its sync-to-async
    bridge) -- unlike AzureOpenAIClient, which wraps a sync ChatOpenAI client
    directly. Nothing else in this agent's lifecycle owns a persistent
    runtime to share, so one is created per call and closed via close_client(),
    which the caller must use instead of the returned client's own close()
    (that only unregisters from the runtime -- it never stops the runtime's
    own background thread/loop, verified against this wheel's AsyncServiceRuntime).
    """

    def __init__(self, model: str = "text-embedding-3-small") -> None:
        self._model = model

    def create_client(self, state: dict[str, Any]) -> BaseEmbedding:
        """Create a client without reading secrets from process environment variables."""
        ctx = InvocationContext.from_state(state)
        self._runtime = AsyncServiceRuntime()
        return AzureEmbedding(
            config={
                "base_url": ctx.secrets.require("AZURE_OPENAI_ENDPOINT"),
                "api_key": ctx.secrets.require("AZURE_OPENAI_API_KEY"),
                "model": self._model,
            },
            runtime=self._runtime,
        )

    def close_client(self, client: BaseEmbedding) -> None:
        """Close `client` and its owning runtime -- always call this, not `client.close()`."""
        client.close()
        asyncio.run(self._runtime.aclose())
