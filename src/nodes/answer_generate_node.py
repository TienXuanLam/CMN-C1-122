"""AgentCore Platform v1.0"""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - S-3: output gate before writing to state
#  - Never import from mediator/, api/, or other agents

import json
from typing import Any, Optional

from framework.nodes.function_node import FunctionNode
from framework.security import detect_credentials
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import APIChangeImpactState

_EMPTY_RESULT_MESSAGE = "No matching API records found for the given query."


class AnswerGenerateNode(FunctionNode):
    """Renders impact analysis into a markdown report with S-3 output gate."""

    required_trust_level = TrustLevel.ANONYMOUS

    def execute(self, state: APIChangeImpactState) -> dict[str, Any]:
        emit_trace_event(
            event_type="answer_generate_start",
            payload={},
            state=state,
        )

        if state.get("error"):
            return {}

        # Empty-result short-circuit
        if (state.get("retrieval_count") or 0) == 0:
            gate_error = self._run_output_gate(_EMPTY_RESULT_MESSAGE)
            if gate_error is not None:
                return gate_error
            emit_trace_event(
                event_type="answer_generate_complete",
                payload={"path": "empty_result"},
                state=state,
            )
            return {
                "output": _EMPTY_RESULT_MESSAGE,
                "formatted_output": _EMPTY_RESULT_MESSAGE,
                "status": AgentStatus.SUCCESS.value,
            }

        raw_impact = state.get("impact_analysis")
        raw_integrations = state.get("affected_integrations")
        if not raw_impact or not raw_integrations:
            return {
                "error": "AnswerGenerateNode: impact_analysis or affected_integrations missing",
                "status": AgentStatus.ERROR.value,
            }

        impact: dict[str, Any] = json.loads(raw_impact)
        integrations: list[dict[str, Any]] = json.loads(raw_integrations)

        rows = (
            "\n".join(
                f"| {self._escape_cell(i['name'])} | {i['severity']} "
                f"| {self._escape_cell(i['reason'])} | {self._escape_cell(i['recommended_action'])} |"
                for i in integrations
            )
            or "| — | — | — | — |"
        )

        steps = (
            "\n".join(f"{n + 1}. {step}" for n, step in enumerate(impact.get("remediation_steps", [])))
            or "No remediation steps provided."
        )

        output = (
            f"## API Change Impact Report\n\n"
            f"**Query**: {self._escape_inline(state.get('query', ''))}\n"
            f"**Max Severity**: {impact['max_severity']}\n"
            f"**Affected Integrations**: {int(impact['blast_radius'])}\n\n"
            f"### Summary\n"
            f"{impact['summary'].strip()}\n\n"
            f"### Affected Integrations\n\n"
            f"| Integration | Severity | Reason | Recommended Action |\n"
            f"|-------------|----------|--------|--------------------|\n"
            f"{rows}\n\n"
            f"### Remediation Steps\n"
            f"{steps}\n\n"
            f"---"
        )

        gate_error = self._run_output_gate(output)
        if gate_error is not None:
            return gate_error

        emit_trace_event(
            event_type="answer_generate_complete",
            payload={"output_len": len(output), "severity": impact.get("max_severity")},
            state=state,
        )

        return {
            "output": output,
            "formatted_output": output,
            "status": AgentStatus.SUCCESS.value,
        }

    def _run_output_gate(self, output: str) -> Optional[dict[str, Any]]:
        """S-3 output gate: credential/injection scan."""
        if detect_credentials(output):
            return {
                "error": "S-3 violation: credential pattern detected in output — redacted",
                "status": AgentStatus.ERROR.value,
            }
        return None

    @staticmethod
    def _escape_cell(value: str) -> str:
        return str(value).replace("|", "\\|")

    @staticmethod
    def _escape_inline(value: str) -> str:
        for ch in ("\\", "*", "_", "[", "]", "`"):
            value = value.replace(ch, f"\\{ch}")
        return value
