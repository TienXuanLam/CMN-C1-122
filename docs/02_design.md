# CMN-C1-122 - Enterprise API Change Impact Q&A Agent — Design Specification

**Template**: CMN-C1-122 Enterprise API Change Impact Q&A Agent
**L1 Base**: `AgentBaseGraph` (L1-direct per 2026-05-18 policy)
**SDK**: `agenticstar-agentcore[marketplace,openai]==1.0.3`
**Stage**: Released (current-scaffold migration)
**Author**: TienTV34
**Created**: 2026-06-01
**Updated**: 2026-06-23 (post-migration, structure aligned with the current fleet scaffold)

---

## 1. Overview

This document defines the design specification for CMN-C1-122. The agent enables
developer and integration teams to assess the blast radius of API changes by
querying an internal API inventory knowledge base (KB), identifying affected
integrations, and generating a structured impact report.

Reference: [01_proposal.md](01_proposal.md)

---

## 2. Architecture Placement

```
Level 0: agenticstar-platform SDK       ← NOT imported
Level 1: framework/                     ← Inherited directly (AgentBaseGraph, FunctionNode)
Level 2: VectorRAGAgent pattern         ← Pattern classification only (not an import target)
Level 3: agents/domain/CMN-C1-122/      ← THIS TEMPLATE
```

`VectorRAGAgent` is the Level 2 pattern classification for this template; the
implementation inherits directly from L1 (`AgentBaseGraph`, `FunctionNode`) per the
2026-05-18 L1-direct policy. No Level 0 imports. No `agents.base.*` imports.

---

## 3. Node Flow

```
QueryEmbedNode → MainNode (APIKBRetrieveNode → ImpactAnalyzeNode) → AnswerGenerateNode
                     │                                                        ↑
                     └──── (retrieval_count == 0) ──────────────────────────┘
```

| Node | SDK Slot | Responsibility | Trust Level |
|------|----------|---------------|-------------|
| `QueryEmbedNode` | `pre_process` | S-2 gate, sanitise, embed query | `VERIFIED_EXTERNAL` |
| `MainNode` | `main` | Composite: APIKBRetrieveNode → ImpactAnalyzeNode | `VERIFIED_EXTERNAL` |
| `AnswerGenerateNode` | `post_process` | S-3 gate, markdown report, `formatted_output` | `ANONYMOUS` |
| `APIKBRetrieveNode` | (inside MainNode) | Qdrant vector search | `ANONYMOUS` |
| `ImpactAnalyzeNode` | (inside MainNode) | LLM-based impact analysis | `ANONYMOUS` |

**Input pattern:** `agent.invoke(user_input=query, ctx=ctx)` — `user_input` is the single public data channel; both the real Marketplace runner and the standalone HTTP adapter forward only this field.

**Config injection (config.yaml → self.config → register_nodes()):**
- `embedding_model` → `QueryEmbedNode`
- `retrieval_score_threshold`, `retrieval_top_k`, `qdrant_collection`, `llm` → `MainNode` → inner nodes

### 3-1. Conditional Flow (inside MainNode)

| From | Condition | To | Behaviour |
|------|-----------|----|-----------|
| `APIKBRetrieveNode` | `retrieval_count == 0` | early return | Skip `ImpactAnalyzeNode`; `AnswerGenerateNode` detects empty and emits no-match message |
| `APIKBRetrieveNode` | `retrieval_count > 0` | `ImpactAnalyzeNode` | Normal path |

**Implementation note**: `MainNode.execute()` calls `APIKBRetrieveNode` first, inspects
`retrieval_count` in the accumulated result, and conditionally calls `ImpactAnalyzeNode`.
Empty retrieval returns early without LLM call. `AnswerGenerateNode` detects
`retrieval_count == 0` and emits a standard no-match message.

---

## 4. State Definition

State is a flat `TypedDict` extending `AgentState`. All fields MUST be primitives
or JSON-serializable types. No Pydantic models, dataclasses, or Python objects.

```python
# src/schemas/state.py
from typing import Any, Optional
from framework.schemas.agent_state import AgentState


class APIChangeImpactState(AgentState):
    query: Optional[str]
    query_embedding: Optional[str]       # json.dumps(list[float])
    query_normalized: Optional[str]
    retrieved_api_records: Optional[str]  # json.dumps(list[dict])
    retrieval_count: Optional[int]
    impact_analysis: Optional[str]        # json.dumps(impact dict)
    affected_integrations: Optional[str]  # json.dumps(list[dict])
    output: Optional[str]
    formatted_output: Optional[str]  # json.dumps(output envelope)
    error: Optional[str]
```

**Serialization rule**: All complex types (lists, dicts) stored as `json.dumps()` strings
including `formatted_output`, which is serialized with `json.dumps()` for checkpoint safety.

**Prohibited in State**: JWT tokens, API keys, Pydantic instances, `InvocationContext` objects.
Credentials accessed via `ctx.secrets.require("KEY")` inside `execute()`.

---

## 5. Node Design

### 5-1. QueryEmbedNode

**File**: `src/nodes/query_embed_node.py`

**Responsibility**: S-2 input gate, sanitise, and embed the user query.

**Reads from state**: `user_input`
**Writes to state**: `query`, `query_embedding`, `query_normalized`

**Key logic**:
1. `emit_trace_event(event_type="query_embed_start", ...)` — module-level call
2. `_run_input_gates(query)` — empty check + credential pattern detection; returns `dict | None`
3. `_sanitize(query)` — strip control characters
4. `_embed(text, state)` — delegates to `src.services.azure_embedding_service.AzureEmbeddingService.create_client(state)` (mirrors `AzureOpenAIService.create_client()` elsewhere in this fleet), which reads `ctx.secrets.require("AZURE_OPENAI_API_KEY")`/`AZURE_OPENAI_ENDPOINT` via `InvocationContext.from_state(state)` and builds an `AzureEmbedding` client; closed per call via `AzureEmbeddingService.close_client()` (`AzureEmbedding` needs its own `AsyncServiceRuntime`, unlike the sync `AzureOpenAIClient`)
5. `emit_trace_event(event_type="query_embed_complete", ...)`
6. Return `{"query": ..., "query_embedding": json.dumps(...), "query_normalized": ...}`

**Error handling**: `_run_input_gates()` returns error dict (not raises). Upstream error
propagates via `if state.get("error"): return {}`.

**Secrets**: `ctx.secrets.require("AZURE_OPENAI_API_KEY")` / `AZURE_OPENAI_ENDPOINT` — never `os.environ`.

---

### 5-2. APIKBRetrieveNode

**File**: `src/nodes/api_kb_retrieve_node.py`

**Responsibility**: Retrieve API inventory records from Qdrant using the embedded query.
PB-3 boundary (framework → external service).

**Reads from state**: `query_embedding`
**Writes to state**: `retrieved_api_records`, `retrieval_count`

**Key logic**:
1. Guard: `if state.get("error"): return {}`
2. **TEMPORARY (2026-09-11, see `docs/06_release_note.md`)**: if no `qdrant_client` is constructor-injected and `ctx.secrets.get("QDRANT_URL")` is unset, return a hardcoded, illustrative `_FIXTURE_RECORDS` list instead of erroring — no Qdrant instance is provisioned yet. Remove this branch once one exists.
3. Otherwise: `ctx.secrets.require("QDRANT_URL")` via `InvocationContext.from_state(state)` — no localhost fallback
4. Deserialize `json.loads(state["query_embedding"])` → vector search against `self._collection` (constructor config, not caller-supplied)
5. Filter by `score >= threshold`; store `retrieved_api_records` as `json.dumps(list[dict])`
6. Return `{"retrieved_api_records": ..., "retrieval_count": N}`

**Retrieval schema per record**:

```json
{
  "endpoint": "string",
  "api_version": "string",
  "consumer_systems": ["string"],
  "owner_team": "string",
  "last_updated": "ISO8601 date string",
  "change_type": "DEPRECATION | BREAKING | NON_BREAKING | NEW",
  "score": "float"
}
```

---

### 5-3. ImpactAnalyzeNode

**File**: `src/nodes/impact_analyze_node.py`

**Responsibility**: LLM-based reasoning over retrieved API records to determine
severity, blast radius, and remediation paths.

**Reads from state**: `query`, `retrieved_api_records`
**Writes to state**: `impact_analysis`, `affected_integrations`

**Key logic**:
1. Guard: `if state.get("error"): return {}`
2. Early return `{}` if `retrieved_api_records == "[]"` (no LLM call)
3. `llm.complete([{"role": "user", "content": prompt}])` → `{"content": str}`
4. Parse JSON; correct `blast_radius` to `len(affected_integrations)` if off
5. Store `impact_analysis` as `json.dumps(impact_dict)`, `affected_integrations` as `json.dumps(list[dict])`
6. Return both fields

**LLM output schema**:

```json
{
  "summary": "string",
  "max_severity": "CRITICAL | HIGH | MEDIUM | LOW",
  "blast_radius": "integer",
  "affected_integrations": [
    {
      "name": "string",
      "severity": "CRITICAL | HIGH | MEDIUM | LOW",
      "reason": "string",
      "recommended_action": "string"
    }
  ],
  "remediation_steps": ["string"]
}
```

**LLM construction**: `_build_llm(state)` builds a fresh, secret-bound `AzureOpenAIClient`
per invocation via `src.services.azure_openai_service.AzureOpenAIService.create_client(state)`
(mirrors `AzureEmbeddingService` and the equivalent pattern elsewhere in this fleet) --
not constructor injection. `config["llm"]` is never read: neither `cli.py` (the real
Marketplace entrypoint) nor `run_agent_marketplace()` ever populates it, so a
constructor-injected client would always be `None` on the real Marketplace path
(verified against the installed 1.0.3 wheel). `STG_MOCK_MODE=true` makes `_build_llm()`
return a deterministic `_MockLLM` instead (STG-tier wiring tests only).

---

### 5-4. AnswerGenerateNode

**File**: `src/nodes/answer_generate_node.py`

**Responsibility**: Render structured analysis into a markdown impact report;
apply S-3 output gate; set `formatted_output` for SDK `result["output"]`.

**Reads from state**: `query`, `impact_analysis`, `affected_integrations`, `retrieval_count`
**Writes to state**: `output`, `formatted_output`, `status`

**Key logic**:
1. Guard: `if state.get("error"): return {}`
2. If `retrieval_count == 0`: emit no-match path; return `{"output": _EMPTY_RESULT_MESSAGE, "formatted_output": {...}, "status": "success"}`
3. Parse `impact_analysis` and `affected_integrations`
4. Render markdown report (see §5-4-1)
5. `_run_output_gate(output)` — S-3 credential/JWT scan; returns `dict | None` (not raises)
6. Emit `emit_trace_event(event_type="answer_generate_complete", ...)`
7. Return `{"output": ..., "formatted_output": envelope, "status": AgentStatus.SUCCESS.value}`

#### 5-4-1. Report Structure

```markdown
## API Change Impact Report

**Query**: {query}
**Max Severity**: {max_severity}
**Affected Integrations**: {blast_radius}

### Summary
{summary}

### Affected Integrations

| Integration | Severity | Reason | Recommended Action |
|-------------|----------|--------|--------------------|
| {name}      | {severity} | {reason} | {recommended_action} |

### Remediation Steps
1. {step}

---
```

Pipe characters (`|`) in cell values are escaped with `_escape_cell()`.
Inline markdown in query string is escaped with `_escape_inline()`.

---

## 6. Graph Definition

**File**: `src/graph/graph.py`

```python
class APIChangeImpactGraph(AgentBaseGraph):
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
            embedding_model=self.config.get("embedding_model", "text-embedding-3-small")
        )
        self._nodes["main"] = MainNode(
            score_threshold=float(self.config.get("retrieval_score_threshold", 0.75)),
            top_k=int(self.config.get("retrieval_top_k", 5)),
            collection=self.config.get("qdrant_collection", "api_inventory"),
            llm_temperature=float(self.config.get("llm_temperature", 0.2)),
            llm_max_tokens=int(self.config.get("llm_max_tokens", 4096)),
            timeout_s=int(self.config.get("timeout_s", 60)),
            max_retry=int(self.config.get("max_retry", 2)),
        )
        self._nodes["post_process"] = AnswerGenerateNode()
```

**Invocation pattern:**
```python
agent = APIChangeImpactGraph()
agent.compile()
result = agent.invoke(
    user_input=query,
    ctx=ctx,
)
# result["status"], result["output"]
```

---

## 7. Security Model Compliance

| Layer | Hook | Implementation |
|-------|------|----------------|
| S-1 | `required_trust_level` | `VERIFIED_EXTERNAL` on graph + QueryEmbedNode/MainNode; framework enforces at runtime |
| S-2 | `_run_input_gates()` | Credential + empty check in `QueryEmbedNode`; returns error dict (not raises) |
| S-3 | `_run_output_gate()` | Credential + JWT scan in `AnswerGenerateNode`; returns error dict (not raises) |
| S-4 | `emit_trace_event()` | Module-level call in every node; `shared.utils.audit_logger` |
| S-5 | Framework `__init_subclass__` | Automatic; no JWT in class vars or test fixtures |

**Note — security gate naming**: `FunctionNode._security_gate_input/output` are `@final`
in the production SDK. Domain gate logic uses private helper methods (`_run_input_gates`,
`_run_output_gate`) to avoid the naming conflict.

---

## 8. External Dependencies

| Dependency | Purpose | Access method |
|------------|---------|--------------|
| Qdrant | Vector KB for API inventory records | `ctx.secrets.require("QDRANT_URL")` |
| Azure OpenAI Embedding | Query embedding | `ctx.secrets.require("AZURE_OPENAI_API_KEY")` / `AZURE_OPENAI_ENDPOINT` |
| LLM (BaseLLM) | Impact analysis | Injected via constructor; `AzureOpenAIClient` sourced from `AZURE_OPENAI_API_KEY`/`AZURE_OPENAI_ENDPOINT`/`AZURE_OPENAI_DEPLOYMENT` |

Credentials accessed exclusively via `ctx.secrets.require("KEY")`. Never `os.environ`.

---

## 9. Config Manifest

**File**: `config/agent.yaml`

```yaml
template_id: CMN-C1-122
name: cmn-c1-122
version: "1.0.0"
category: 1
required_trust_level: VERIFIED_EXTERNAL
requires:
  extras: [openai, platform-rag]
  secrets: [AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT, QDRANT_URL]
```

**File**: `config/config.yaml`

```yaml
max_retry: 2
memory_enabled: false
timeout_s: 60
retrieval_score_threshold: 0.75
retrieval_top_k: 5
qdrant_collection: api_inventory
llm_model: gpt-4o-mini
embedding_model: text-embedding-3-small
embedding_dimensions: 1536
```

---

## 10. Test Coverage Plan

Full TC/PB items are specified in [03_test_spec.md](03_test_spec.md). Summary:

| Test group | Files | Gate |
|------------|-------|------|
| Unit (per-node) | `tests/unit/test_nodes.py`, `tests/unit/test_main_node.py` | All pass required |
| Security gates | `tests/unit/test_security_gates.py` | TC-S2 + TC-S3 |
| Proof-of-Boundary | `tests/proof_of_boundary/test_pb_placeholder.py`, `test_import_isolation.py`, `test_state_safety.py` | All PB-1–PB-6 pass |
| Integration smoke | `tests/integration/test_graph_smoke.py` | Compile + invoke pass |

---

## 11. Open Items

| # | Item | Owner | Status |
|---|------|-------|--------|
| D-01 | Confirm Qdrant collection schema — `change_type` field availability and index config | TienTV34 | Open — Stage ③ |
| D-02 | Embedding model version and dimension | TienTV34 | ✅ Resolved: `text-embedding-3-small`, dim=1536 |
| D-03 | PII masking strategy: mask-and-continue vs. reject-with-error | TienTV34 | Open — Stage ③ |
| D-04 | `add_conditional_edges` availability | TienTV34 | ✅ Resolved: using MainNode composite (FunctionNode) instead |

---

*Stage gate ② → ③ condition: this file committed to `develop` branch.*
