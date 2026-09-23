"""AgentCore Platform v1.0"""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - LLM built per-invocation via _build_llm(state) -- see its docstring for
#    why this replaced constructor injection.
#  - Never import from mediator/, api/, or other agents

import json
import os
from typing import Any

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.services.llm.base_llm import BaseLLM
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import APIChangeImpactState
from src.services.azure_openai_service import AzureOpenAIService

_REQUIRED_KEYS = ("summary", "max_severity", "blast_radius", "affected_integrations", "remediation_steps")
_VALID_SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
_REQUIRED_INTEGRATION_KEYS = ("name", "severity", "reason", "recommended_action")

_PROMPT_TEMPLATE = """You are an API change impact analyst.

Given the following user query and a list of API inventory records, analyze the
impact of the described API change. Identify affected integrations, assess severity,
and recommend remediation steps.

User Query:
{query}

API Records:
{records}

Respond ONLY with a valid JSON object matching this exact schema — no prose, no markdown:
{{
  "summary": "<string>",
  "max_severity": "<CRITICAL|HIGH|MEDIUM|LOW>",
  "blast_radius": <integer>,
  "affected_integrations": [
    {{
      "name": "<string>",
      "severity": "<CRITICAL|HIGH|MEDIUM|LOW>",
      "reason": "<string>",
      "recommended_action": "<string>"
    }}
  ],
  "remediation_steps": ["<string>"]
}}"""


class _MockLLM(BaseLLM):
    """STG_MOCK_MODE=true stand-in -- deterministic, no network call.

    STG-tier wiring tests only; must never be reachable in production (see
    STG_MOCK_MODE handling in _build_llm()).
    """

    def complete(self, _messages: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "content": json.dumps(
                {
                    "summary": "STG_MOCK_MODE stand-in response.",
                    "max_severity": "LOW",
                    "blast_radius": 0,
                    "affected_integrations": [],
                    "remediation_steps": [],
                }
            )
        }

    def stream(self, _messages: list[Any]) -> Any:
        raise NotImplementedError("_MockLLM: stream() is not used by ImpactAnalyzeNode")

    def bind_tools(self, _tools: list[Any]) -> "BaseLLM":
        raise NotImplementedError("_MockLLM: bind_tools() is not used by ImpactAnalyzeNode")


class ImpactAnalyzeNode(FunctionNode):
    """LLM-based API change impact analysis."""

    required_trust_level = TrustLevel.ANONYMOUS

    def __init__(
        self,
        llm_temperature: float = 0.2,
        llm_max_tokens: int = 4096,
        timeout_s: int = 120,
        max_retry: int = 2,
    ):
        self._llm_service = AzureOpenAIService(
            llm_temperature=llm_temperature,
            llm_max_tokens=llm_max_tokens,
            timeout_s=timeout_s,
            max_retry=max_retry,
        )

    def _build_llm(self, state: dict[str, Any]) -> BaseLLM:
        """Build a fresh, secret-bound LLM client for this invocation.

        Constructor-time injection (an `llm_client` param set once when the
        graph registers this node) depends on `config["llm"]` being
        populated before `agent_cls(config=...)` runs -- but neither
        `cli.py` (the real Marketplace entrypoint) nor
        `shared.bootstrap.marketplace_app.run_agent_marketplace` ever sets
        it (verified against the installed 1.0.3 wheel: `config` is passed
        straight through, with a comment noting the `config["llm"]` seam is
        the *caller's* responsibility to fill before calling it). A
        constructor-injected client would therefore always be `None` on the
        real Marketplace path. Building per-invocation here instead mirrors
        the pattern already used elsewhere in this fleet
        (`AzureOpenAIService.create_client(state)`), and reads secrets from
        `state` at call time rather than depending on `config` at all.

        `STG_MOCK_MODE=true` returns a deterministic `_MockLLM` instead of a
        real client -- STG-tier wiring tests only, must never be set in
        production. This is the scaffold's standard Stage 5
        provisional-deploy toggle, set "true" by the shared deploy-stg CI
        job; the same variable server.py already reads to mock
        Qdrant/embedding.
        """
        if os.environ.get("STG_MOCK_MODE", "").lower() == "true":
            return _MockLLM()
        return self._llm_service.create_client(state)

    def execute(self, state: APIChangeImpactState) -> dict[str, Any]:
        emit_trace_event(
            event_type="impact_analyze_start",
            payload={},
            state=state,
        )

        if state.get("error"):
            return {}

        raw_records = state.get("retrieved_api_records")
        if not raw_records:
            return {
                "error": "ImpactAnalyzeNode: retrieved_api_records missing",
                "status": AgentStatus.ERROR.value,
            }
        records: list[dict[str, Any]] = json.loads(raw_records)

        # Empty records — AnswerGenerateNode handles no-match response
        if not records:
            return {}

        query = state.get("query", "")
        prompt = _PROMPT_TEMPLATE.format(
            query=query,
            records=json.dumps(records, indent=2, ensure_ascii=False),
        )

        try:
            llm = self._build_llm(state)
            response = llm.complete([{"role": "user", "content": prompt}])
            raw_response = response.get("content", "") if isinstance(response, dict) else str(response)
        except Exception as exc:  # noqa: BLE001
            return {
                "error": f"ImpactAnalyzeNode: LLM call failed — {type(exc).__name__}",
                "status": AgentStatus.ERROR.value,
            }

        try:
            result = json.loads(raw_response.strip())
        except json.JSONDecodeError as exc:
            return {
                "error": f"ImpactAnalyzeNode: LLM returned malformed JSON — {exc}",
                "status": AgentStatus.ERROR.value,
            }

        for key in _REQUIRED_KEYS:
            if key not in result:
                return {
                    "error": f"ImpactAnalyzeNode: LLM response missing key '{key}'",
                    "status": AgentStatus.ERROR.value,
                }

        if result["max_severity"] not in _VALID_SEVERITIES:
            return {
                "error": f"ImpactAnalyzeNode: invalid max_severity '{result['max_severity']}'",
                "status": AgentStatus.ERROR.value,
            }

        for i, integration in enumerate(result["affected_integrations"]):
            for key in _REQUIRED_INTEGRATION_KEYS:
                if key not in integration:
                    return {
                        "error": f"ImpactAnalyzeNode: affected_integrations[{i}] missing '{key}'",
                        "status": AgentStatus.ERROR.value,
                    }
            if integration["severity"] not in _VALID_SEVERITIES:
                return {
                    "error": f"ImpactAnalyzeNode: affected_integrations[{i}] invalid severity",
                    "status": AgentStatus.ERROR.value,
                }

        # Self-correct blast_radius
        n = len(result["affected_integrations"])
        if result["blast_radius"] != n:
            emit_trace_event(
                event_type="blast_radius_corrected",
                payload={"llm_value": result["blast_radius"], "actual": n},
                state=state,
            )
            result["blast_radius"] = n

        emit_trace_event(
            event_type="impact_analyze_complete",
            payload={"affected": n, "severity": result["max_severity"]},
            state=state,
        )

        return {
            "impact_analysis": json.dumps(result),
            "affected_integrations": json.dumps(result["affected_integrations"]),
        }
