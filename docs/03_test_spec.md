# docs/03_test_spec.md — CMN-C1-122 Test Specification

**Template ID:** CMN-C1-122
**Agent Name:** Enterprise API Change Impact Q&A Agent
**Date:** 2026-06-02
**Version:** 0.5.0 (structure aligned with the current fleet scaffold; test_security_gates.py + test_main_node.py added)
**Author:** F-TienTV34

---

## 1. Framework Compliance Tests (TC)

| TC-ID | Test Description | Node / Scope | Method | Expected Result |
|-------|-----------------|--------------|--------|----------------|
| TC-01 | APIChangeImpactState is a flat TypedDict — no Pydantic, no dataclass | APIChangeImpactState | Inspect all field annotations; instantiate and check value types | All field values are str, int, bool, or NoneType; get_type_hints() shows no class types |
| TC-02 | S-2 gate fires on credential input | QueryEmbedNode | Inject "sk-abc123xyz" as query; call execute() | Error dict returned with status=error; `_embed()` never called |
| TC-03 | No credential field in State after full invoke | Full pipeline | Run pipeline with mock nodes; inspect all state fields via regex scan | 0 fields match sk-, Bearer , eyJ, AKIA patterns |
| TC-04 | InvocationContext not stored in State | All nodes | Post-invoke state key scan | "invocation_context" not in state.keys() |
| TC-05 | _emit_trace_event() fires on every node | All 4 nodes | Mock _emit_trace_event; run full pipeline | Mock called exactly 4 times (once per node, normal path) |
| TC-06 | S-2 gate runs even if query_normalized preset | QueryEmbedNode | Pre-set state["query_normalized"] = "cached"; inject credential query | SecurityViolationError raised; preset value irrelevant |
| TC-07 | S-3 gate runs on every AnswerGenerateNode invoke | AnswerGenerateNode | Mock _security_gate_output to raise; call execute() | Gate called before state["output"] written; output absent from state |
| TC-08 | required_trust_level enforced at __pre_invoke__ | QueryEmbedNode | Call with TrustLevel.ANONYMOUS when node requires VERIFIED_EXTERNAL | Trust violation raised before execute() body runs. Verified against production SDK (`agenticstar-agentcore==1.0.1`). |

---

## 2. Proof-of-Boundary Tests (PB)

### PB-1: BaseNode → EventEmitter

**File:** tests/proof_of_boundary/test_pb_placeholder.py

**Setup:** Patch module-level `shared.utils.audit_logger.emit_trace_event`. Invoke each node with minimal valid state.

**Assertions:**
- _emit_trace_event call count ≥ 1 per node
- Called with a string argument containing `cid=`
- No AttributeError or silent failure

---

### PB-2: State serialization (msgpack safety)

**File:** tests/proof_of_boundary/test_pb_placeholder.py

**Setup:** Run full pipeline with all mocks. Capture final state dict.

**Assertions:**
- All values in state.values() are `str | int | float | bool | None`
- `json.dumps(state)` succeeds without TypeError
- No value is a Pydantic model, dataclass, or arbitrary Python object

---

### PB-3: L1 framework → Qdrant (external boundary)

**File:** tests/proof_of_boundary/test_pb_placeholder.py

**Setup:** Mock InvocationContext.credential_handle to return a fake client. Mock client.search() to return 2 fixture records.

**Assertions:**
- APIKBRetrieveNode calls client.search() exactly once
- state["retrieved_api_records"] is valid JSON containing 2 records
- state["retrieval_count"] == 2
- No direct qdrant_client import in src/nodes/api_kb_retrieve_node.py (AST check)

---

### PB-4: Import isolation (L3 must not import L0)

**File:** tests/proof_of_boundary/test_pb_placeholder.py

**Method:** AST scan all .py files under src/.

**Assertions:**
- 0 occurrences of `import agenticstar`
- 0 occurrences of `from agenticstar`
- 0 occurrences of `from agents.base` (L2 import — retired per 2026-05-18 policy)

---

### PB-5: Checkpoint safety

**File:** tests/proof_of_boundary/test_pb_placeholder.py

**Setup:** Run full pipeline with mocks. Serialize final state to JSON (simulates checkpoint).

**Assertions:**
- No field value matches JWT pattern `eyJ[A-Za-z0-9._-]+`
- No field value matches `sk-[a-zA-Z0-9]{20,}`
- `json.dumps(state)` round-trips cleanly: `json.loads(json.dumps(state)) == state`

---

### PB-6: Node execution order

**File:** tests/proof_of_boundary/test_pb_placeholder.py

**Setup:** Use unittest.mock.patch to record call order of `_run_input_gates`, `execute`, `_run_output_gate`, `emit_trace_event` on QueryEmbedNode.

**Assertions:**
- SDK enforces: `__pre_invoke__` → `execute()` → output gate → `emit_trace_event`
- Domain gates (`_run_input_gates`, `_run_output_gate`) called inside `execute()`
- No step skipped or called out of order

---

## 3. Unit Tests — Per Node

### 3-1. QueryEmbedNode

**Mock requirements:** `patch.object(node, "_embed", return_value=[0.1] * 1536)`

| UT-ID | Scenario | Setup | Expected Result |
|-------|----------|-------|----------------|
| UT-Q-01 | Happy path | Valid query, mock `_embed` returns [0.1]*1536 | query_embedding = json.dumps([0.1]*1536); query_normalized = stripped/lowercased query |
| UT-Q-02 | S-2 — credential pattern in query | Query contains `api_key=secret123` | Error dict returned; `_embed()` not called |
| UT-Q-03 | S-2 — bearer token | Query = "Bearer eyJhbGci..." | Error dict with S-2 violation message |
| UT-Q-04 | Empty query | state["user_input"] = "" | Error dict returned; status=error |
| UT-Q-05 | Whitespace-only query | state["user_input"] = "   " | Error dict returned (empty after strip) |
| UT-Q-06 | Upstream error propagates | state["error"] = "upstream" | Returns {} immediately |

---

### 3-2. APIKBRetrieveNode

**Mock requirements:** `patch("src.nodes.api_kb_retrieve_node.QdrantClient")` → fake client; `client.search()` → fixture records; `bound_secrets(_SECRETS)` for QDRANT_URL

**Fixture record:**

```json
{
  "score": 0.92,
  "payload": {
    "endpoint": "/v1/payments",
    "api_version": "v54",
    "change_type": "DEPRECATION",
    "consumer_systems": ["checkout", "billing"],
    "owner_team": "payments-team",
    "last_updated": "2026-05-01"
  }
}
```

| UT-ID | Scenario | Setup | Expected Result |
|-------|----------|-------|----------------|
| UT-R-01 | Happy path — records above threshold | client.search() returns 1 record, score ≥ 0.75 | retrieval_count = 1; retrieved_api_records = valid JSON array |
| UT-R-02 | query_embedding missing | state["query_embedding"] = None | Error dict returned |
| UT-R-03 | Upstream error propagates | state["error"] = "upstream" | Returns {} immediately |
| UT-R-05 | trust_level is ANONYMOUS | Node class attribute | `required_trust_level == TrustLevel.ANONYMOUS` |

---

### 3-3. ImpactAnalyzeNode

**Mock requirements:** `mock_llm = MagicMock(); mock_llm.complete.return_value = {"content": json.dumps(fixture)}`; `_build_llm()` has no constructor injection seam -- tests patch the instance method directly: `node = ImpactAnalyzeNode(); node._build_llm = lambda state: mock_llm`

**LLM fixture:**

```json
{
  "summary": "Salesforce API v54 deprecation affects 3 integrations.",
  "max_severity": "CRITICAL",
  "blast_radius": 3,
  "affected_integrations": [
    {"name": "checkout", "severity": "CRITICAL", "reason": "Direct v54 dependency", "recommended_action": "Migrate to v56"},
    {"name": "billing", "severity": "HIGH", "reason": "Indirect dependency", "recommended_action": "Update SDK"},
    {"name": "reporting", "severity": "LOW", "reason": "Read-only usage", "recommended_action": "Monitor"}
  ],
  "remediation_steps": ["Audit all v54 usages", "Update SDK to v56", "Run integration tests"]
}
```

| UT-ID | Scenario | Setup | Expected Result |
|-------|----------|-------|----------------|
| UT-I-01 | Happy path | Valid records, LLM returns fixture JSON | impact_analysis = json.dumps(fixture); affected_integrations = json.dumps([...3 items...]) |
| UT-I-02 | Empty records — early return | state["retrieved_api_records"] = "[]" | No LLM call (mock.complete.assert_not_called()); returns {} |
| UT-I-03 | LLM returns non-JSON | mock.complete returns {"content": "not json"} | Error dict returned |
| UT-I-04 | LLM build failure | `node._build_llm = <raises>` (e.g. missing secret) | Error dict returned |
| UT-I-05 | blast_radius off-by-one | "blast_radius": 99 with 3 integrations | Self-corrected to 3 |
| UT-I-06 | Upstream error propagates | state["error"] = "upstream" | Returns {} immediately |

---

### 3-4. AnswerGenerateNode

| UT-ID | Scenario | Setup | Expected Result |
|-------|----------|-------|----------------|
| UT-A-01 | Happy path | Valid impact_analysis + affected_integrations | output contains all sections: Summary, Affected Integrations, Remediation Steps |
| UT-A-02 | Empty-result path | state["retrieval_count"] = 0 | output = "No matching API records found"; serialized formatted_output contains answer |
| UT-A-03 | impact_analysis missing | state["impact_analysis"] = None | Error dict returned |
| UT-A-04 | S-3 gate blocks output | `patch.object(node, "_run_output_gate", return_value={"error": ..., "status": "error"})` | Gate return propagated; output absent |
| UT-A-05 | Table cell with \| | i["reason"] = "foo\|bar" | Output contains `foo\|bar`; table not broken |
| UT-A-06 | Upstream error propagates | state["error"] = "upstream" | Returns {} immediately |
| UT-A-07 | trust_level is ANONYMOUS | Node class attribute | `required_trust_level == TrustLevel.ANONYMOUS` |

---

## 4. Functional Tests (FT)

| FT-ID | Scenario | Input | Mock | Expected Result |
|-------|----------|-------|------|----------------|
| FT-01 | Happy path — full pipeline | Valid query; 3 KB records; valid LLM response | Qdrant mock + LLM mock | state["output"] = markdown report; state["error"] = None; node_trace = ["QueryEmbedNode","APIKBRetrieveNode","ImpactAnalyzeNode","AnswerGenerateNode"] |
| FT-02 | Empty-result path | Valid query; Qdrant returns 0 records | Qdrant mock returns [] | retrieval_count = 0; output = no-match message; LLM mock never called |
| FT-03 | S-2 gate | Query = "Bearer sk-abc123" | None | SecurityViolationError propagated; state["error"] set by graph |
| FT-04 | S-3 gate | LLM output contains "sk-secret123" | _security_gate_output raises | state["output"] absent or None; state["error"] set |
| FT-05 | LLM malformed JSON | LLM returns "not json" | LLM mock | state["error"] set; graph returns state; output not written |
| FT-06 | DEPRECATION severity | change_type = "DEPRECATION", 5 consumers | LLM mock returns CRITICAL | max_severity = CRITICAL; blast_radius = 5 |
| FT-07 | Credentials via InvocationContext | Normal query | Assert no os.environ call | os.environ.__getitem__ never called during pipeline |
| FT-08 | Below-threshold records | All records score = 0.5, threshold = 0.75 | Qdrant mock | retrieval_count = 0; short-circuit path fires |
| FT-09 | Conflicting change types | Mix of BREAKING + NON_BREAKING records | LLM mock returns CRITICAL | max_severity = CRITICAL; both types reflected in report |
| FT-10 | Integration — end-to-end | Valid query; 2 records; structured LLM response | Full mock chain | output populated; error = None; all 4 nodes in node_trace |

---

## 5. Edge Cases

| EC-ID | Scenario | Expected Result |
|-------|----------|----------------|
| EC-01 | correlation_id missing from state | All nodes fall back to "" via .get("correlation_id", "") — no KeyError |
| EC-02 | node_trace is None in initial state | All nodes fall back to "[]" via `or "[]"` — no json.loads(None) error |
| EC-03 | LLM returns valid JSON but affected_integrations is empty list | blast_radius corrected to 0; report table shows `\| — \| — \| — \| — \|` |
| EC-04 | retrieval_count is None (not 0) | `(state.get("retrieval_count") or 0) == 0` evaluates to True — short-circuit fires |
| EC-05 | query contains only whitespace | query_normalized = "" — downstream nodes receive empty string |
| EC-06 | All 4 nodes raise in sequence | Each exception written to state["error"]; last error persists; node_trace shows progress |
| EC-07 | SecurityViolationError mid-pipeline | Re-raised by APIChangeImpactGraph.run(); not written to state["error"] |

---

## 6. Coverage Targets

| Category | Target | Notes |
|----------|--------|-------|
| TC (Framework Compliance) | 100% | All 8 items must pass |
| PB (Proof of Boundary) | 100% | All 6 items must pass; PB-3 uses mock |
| UT (Unit — per node) | ≥ 80% | All per-node tables; minimum happy path + error path + edge case per node |
| FT (Functional) | ≥ 90% | Minimum 9/10 must pass (raised from 80% — CoE Cat 1 standard) |
| EC (Edge Cases) | ≥ 80% | Minimum 6/7 must pass (raised from 70% — CoE Cat 1 standard) |
| Line coverage (src/) | ≥ 80% | Measured by pytest-cov |

---

## 7. Test Files

| File | Contains |
|------|----------|
| `tests/unit/test_nodes.py` | Per-node unit tests (QueryEmbedNode, APIKBRetrieveNode, ImpactAnalyzeNode, AnswerGenerateNode) |
| `tests/unit/test_main_node.py` | MainNode contract + happy path + error handling |
| `tests/unit/test_security_gates.py` | S-2 (TC-S2-01–06) and S-3 (TC-S3-01–04) gate tests |
| `tests/proof_of_boundary/test_pb_placeholder.py` | PB-1 through PB-6 |
| `tests/proof_of_boundary/test_import_isolation.py` | PB-4: L0/L1 import boundary AST scan |
| `tests/proof_of_boundary/test_state_safety.py` | PB-2/PB-5: msgpack safety, credential fields, AgentState inheritance |
| `tests/integration/test_graph_smoke.py` | Compile + invoke smoke tests |

**Run with:**

```bash
# All tests
python -m pytest tests/ -v --tb=short --cov=src --cov-report=term-missing

# PB only
python -m pytest tests/proof_of_boundary/ -v --tb=short

# Security gates only
python -m pytest tests/unit/test_security_gates.py -v

# Per-node unit tests only
python -m pytest tests/unit/ -v
```

---

## 8. Mock Reference

| Component | Mock target | Return value |
|-----------|-------------|-------------|
| `QueryEmbedNode._embed()` | `patch.object(node, "_embed", return_value=[0.1]*1536)` | `[0.1] * 1536` |
| `APIKBRetrieveNode` Qdrant | `patch("src.nodes.api_kb_retrieve_node.QdrantClient")` | Fake client with `.search()` method |
| `ImpactAnalyzeNode` LLM | `mock_llm = MagicMock(); mock_llm.complete.return_value = {"content": json.dumps(fixture)}` | Valid JSON fixture string (see §3-3) |
| `emit_trace_event` | `patch("src.nodes.<node_module>.emit_trace_event")` | None (side-effect free) |
| Secrets | `bound_secrets(InMemoryProvider({"AZURE_OPENAI_API_KEY": "test-key", "AZURE_OPENAI_ENDPOINT": "https://test.openai.azure.com", "AZURE_OPENAI_DEPLOYMENT": "test-deployment", "QDRANT_URL": "http://localhost:6333"}))` | Context manager |

> ⚠️ Never use real credentials in test fixtures.
> Use `"test-key"` and `"http://localhost:6333"` as placeholders — never real keys.
