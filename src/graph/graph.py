"""AgentCore Platform v1.0"""

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.schemas.trust_level import TrustLevel

from src.nodes.query_embed_node import QueryEmbedNode
from src.nodes.main_node import MainNode
from src.nodes.answer_generate_node import AnswerGenerateNode
from src.schemas.state import APIChangeImpactState


class APIChangeImpactGraph(AgentBaseGraph):
    """Fixed-pipeline graph for CMN-C1-122 EnterpriseAPIChangeImpactAgent.

    Slot mapping (4 conceptual nodes → 3 SDK slots):
      pre_process  → QueryEmbedNode    (S-2 gate, sanitise, embed query)
      main         → MainNode          (APIKBRetrieveNode → ImpactAnalyzeNode)
      post_process → AnswerGenerateNode (S-3 gate, markdown report, formatted_output)

    Domain input is the single public `user_input` string (the real
    Marketplace and standalone-HTTP invoke contract forward only this field):
      agent.invoke(user_input=query, ctx=ctx)
    """

    required_trust_level = TrustLevel.VERIFIED_EXTERNAL

    @property
    def name(self) -> str:
        return "cmn_c1_api_change_impact_agent"

    @property
    def state_schema(self) -> type:
        return APIChangeImpactState

    def register_nodes(self) -> None:
        super().register_nodes()

        self._nodes["pre_process"] = QueryEmbedNode(
            embedding_model=self.config.get("embedding_model", "text-embedding-3-small"),
            embedding_client=self.config.get("embedding"),
        )
        self._nodes["main"] = MainNode(
            score_threshold=float(self.config.get("retrieval_score_threshold", 0.75)),
            top_k=int(self.config.get("retrieval_top_k", 5)),
            collection=self.config.get("qdrant_collection", "api_inventory"),
            llm_temperature=float(self.config.get("llm_temperature", 0.2)),
            llm_max_tokens=int(self.config.get("llm_max_tokens", 4096)),
            timeout_s=int(self.config.get("timeout_s", 60)),
            max_retry=int(self.config.get("max_retry", 2)),
            qdrant_client=self.config.get("qdrant"),
        )
        self._nodes["post_process"] = AnswerGenerateNode()
