# CMN-C1-122 — Unit Tests: S-2 and S-3 security gates
# Covers TC-S2-01–04 and TC-S3-01–04 from docs/03_test_spec.md

from framework.schemas.agent_status import AgentStatus
from framework.secrets.context import bound_secrets
from shared.secrets.inmemory_provider import InMemoryProvider

from src.nodes.query_embed_node import QueryEmbedNode
from src.nodes.answer_generate_node import AnswerGenerateNode

_SECRETS = InMemoryProvider(
    {
        "AZURE_OPENAI_API_KEY": "test-key",
        "AZURE_OPENAI_ENDPOINT": "https://test.openai.azure.com",
        "AZURE_OPENAI_DEPLOYMENT": "test-deployment",
        "QDRANT_URL": "http://localhost:6333",
    }
)


# ---------------------------------------------------------------------------
# TC-S2: S-2 Input Gate (QueryEmbedNode._run_input_gates)
# ---------------------------------------------------------------------------


class TestS2InputGate:
    def test_tc_s2_01_empty_query_blocked(self):
        """TC-S2-01: Empty query → gate blocks with error."""
        node = QueryEmbedNode()
        result = node._run_input_gates("")
        assert result is not None
        assert result.get("error") is not None
        assert result.get("status") == AgentStatus.ERROR.value

    def test_tc_s2_02_whitespace_only_blocked(self):
        """TC-S2-02: Whitespace-only query → blocked."""
        node = QueryEmbedNode()
        result = node._run_input_gates("   ")
        assert result is not None
        assert result.get("status") == AgentStatus.ERROR.value

    def test_tc_s2_03_api_key_pattern_blocked(self):
        """TC-S2-03: api_key credential pattern in query → S-2 violation."""
        node = QueryEmbedNode()
        result = node._run_input_gates("api_key=sk-" + "a" * 25)
        assert result is not None
        assert "S-2 violation" in result["error"]
        assert result.get("status") == AgentStatus.ERROR.value

    def test_tc_s2_04_bearer_token_blocked(self):
        """TC-S2-04: Bearer token in query → S-2 violation."""
        node = QueryEmbedNode()
        result = node._run_input_gates("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9")
        assert result is not None
        assert "S-2 violation" in result["error"]

    def test_tc_s2_05_clean_query_passes(self):
        """TC-S2-05: Normal business query → gate passes (returns None)."""
        node = QueryEmbedNode()
        result = node._run_input_gates("Which integrations break if Salesforce API v54 is deprecated?")
        assert result is None

    def test_tc_s2_06_sk_prefix_key_blocked(self):
        """TC-S2-06: sk-* style credential detected via execute path."""
        node = QueryEmbedNode()
        state = {
            "user_input": "sk-" + "a" * 25,
            "error": None,
            "caller_trust_level": "VERIFIED_EXTERNAL",
            "caller_id": "test",
            "correlation_id": "test",
            "session_id": "test",
            "thread_id": "test",
            "trace_id": "",
            "node_history": [],
            "error_log": [],
            "hitl_allowed": True,
        }
        with bound_secrets(_SECRETS):
            result = node.execute(state)
        assert result.get("error") is not None
        assert result.get("status") == AgentStatus.ERROR.value


# ---------------------------------------------------------------------------
# TC-S3: S-3 Output Gate (AnswerGenerateNode._run_output_gate)
# ---------------------------------------------------------------------------


class TestS3OutputGate:
    def test_tc_s3_01_api_key_in_output_blocked(self):
        """TC-S3-01: API key pattern in output → S-3 violation."""
        node = AnswerGenerateNode()
        result = node._run_output_gate("api_key=sk-abcdefghijklmnopqrstuvwx1234")
        assert result is not None
        assert "S-3 violation" in result["error"]
        assert result.get("status") == AgentStatus.ERROR.value

    def test_tc_s3_02_jwt_in_output_blocked(self):
        """TC-S3-02: JWT token in output → S-3 violation."""
        node = AnswerGenerateNode()
        result = node._run_output_gate("token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig")
        assert result is not None
        assert "S-3 violation" in result["error"]
        assert result.get("status") == AgentStatus.ERROR.value

    def test_tc_s3_03_aws_key_pattern_blocked(self):
        """TC-S3-03: AWS access key pattern in output → S-3 violation."""
        node = AnswerGenerateNode()
        result = node._run_output_gate("AKIA" + "A" * 16)
        assert result is not None
        assert "S-3 violation" in result["error"]

    def test_tc_s3_04_sk_key_in_output_blocked(self):
        """TC-S3-04: sk-* API key in output → S-3 violation."""
        node = AnswerGenerateNode()
        result = node._run_output_gate("The key is sk-" + "a" * 25 + " use it carefully")
        assert result is not None
        assert "S-3 violation" in result["error"]
        assert result.get("status") == AgentStatus.ERROR.value

    def test_tc_s3_05_clean_output_passes(self):
        """TC-S3-05: Clean markdown impact report → gate passes (returns None)."""
        node = AnswerGenerateNode()
        clean = (
            "## API Change Impact Report\n\n"
            "**Query**: which integrations break?\n"
            "**Max Severity**: CRITICAL\n\n"
            "### Summary\nAPI v54 deprecation affects checkout.\n"
        )
        result = node._run_output_gate(clean)
        assert result is None
