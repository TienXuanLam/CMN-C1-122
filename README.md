# CMN-C1-122 — Enterprise API Change Impact Q&A Agent

> **Category**: Cat 1 (delivers a single technical capability, generic and use-case-agnostic)
> **Industry**: CMN (common / cross-industry)

## Overview

Answers developer and integration team questions about API changes using a maintained
internal API inventory knowledge base. Given a plain-text question, the agent embeds
the query, retrieves relevant API inventory records, and analyzes which integrations
break on API deprecations, mandate changes, or version updates.

Query embedding and knowledge-base retrieval run against a Qdrant instance. **No
instance is provisioned yet** — until one exists, retrieval falls back to a small
hardcoded, illustrative fixture instead of erroring, so responses do not reflect real
data. Provision `QDRANT_URL` and index a real API inventory collection before relying
on this template's answers.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |
| Azure OpenAI | A chat-capable deployment, and separately an embedding-capable deployment for query embedding — `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT` |
| Qdrant | An instance for the API inventory knowledge base — `QDRANT_URL` |

```bash
pip install -e .
```

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and operational documentation
```

See `docs/` for the design spec and test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Replace the knowledge sources and sample data with your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
