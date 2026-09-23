"""AgentCore Platform v1.0"""

# ADR-005: State must be a flat TypedDict — never Pydantic BaseModel.
# LangGraph checkpoints use msgpack serialization; Pydantic objects
# cause silent corruption. Extend AgentState with agent-specific
# fields only. Do NOT add credentials, secrets, or Pydantic models.

from typing import Optional

from framework.schemas.agent_state import AgentState


class APIChangeImpactState(AgentState):
    """Agent state for CMN-C1-122 EnterpriseAPIChangeImpactAgent.

    All shared fields (user_input, status, session_id, node_history,
    error_log, hitl_*, etc.) are inherited from AgentState.

    Field naming convention:
      - Input:      query, query_embedding, query_normalized
      - Retrieval:  retrieved_api_records, retrieval_count
      - Analysis:   impact_analysis, affected_integrations
      - Output:     output, formatted_output
      - Errors:     error
    """

    # ---------- Input ----------
    query: Optional[str]
    query_embedding: Optional[str]  # json.dumps(list[float])
    query_normalized: Optional[str]  # lowercased/stripped

    # ---------- Retrieval ----------
    retrieved_api_records: Optional[str]  # json.dumps(list[dict])
    retrieval_count: Optional[int]

    # ---------- Analysis ----------
    impact_analysis: Optional[str]  # json.dumps(impact dict)
    affected_integrations: Optional[str]  # json.dumps(list[dict])

    # ---------- Output ----------
    output: Optional[str]  # final markdown report
    formatted_output: Optional[str]  # same markdown text as `output` (public contract)

    # ---------- Error propagation ----------
    error: Optional[str]
