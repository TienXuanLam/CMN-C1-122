"""Azure OpenAI client construction for CMN-C1-122."""

from typing import Any

from framework.schemas.invocation_context import InvocationContext
from shared.services.llm.azure_openai_client import AzureOpenAIClient


class AzureOpenAIService:
    """Build the AgentCore Azure OpenAI client from invocation-scoped secrets."""

    def __init__(
        self,
        llm_temperature: float = 0.2,
        llm_max_tokens: int = 4096,
        timeout_s: int = 120,
        max_retry: int = 2,
    ) -> None:
        self._llm_temperature = llm_temperature
        self._llm_max_tokens = llm_max_tokens
        self._timeout_s = timeout_s
        self._max_retry = max_retry

    def create_client(self, state: dict[str, Any]) -> AzureOpenAIClient:
        """Create a client without reading secrets from process environment variables."""
        ctx = InvocationContext.from_state(state)
        llm = AzureOpenAIClient(
            {
                "api_key": ctx.secrets.require("AZURE_OPENAI_API_KEY"),
                "azure_endpoint": ctx.secrets.require("AZURE_OPENAI_ENDPOINT"),
                "azure_deployment": ctx.secrets.require("AZURE_OPENAI_DEPLOYMENT"),
                "temperature": self._llm_temperature,
                "max_tokens": self._llm_max_tokens,
                "timeout": self._timeout_s,
                "max_retries": self._max_retry,
            }
        )
        return llm
