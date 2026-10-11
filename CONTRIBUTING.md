# Contributing

Start with [requirements](docs/REQUIREMENTS.md), [the handoff](docs/HANDOFF.md), [README.md](README.md), [interfaces](docs/INTERFACES.md) and [analysis guidance](ANALYSIS_GUIDE.md).

## Useful contributions

- Reproduce retrieval failures using synthetic or explicitly public repositories, exact queries, prerequisites and source-backed expected results.
- Improve capability evidence and per-feature boundaries; preserve pinned commits and disclose inspection/runtime limits.
- Improve onboarding, Windows/Linux compatibility, accessibility and MCP interoperability.
- Keep Chinese and English command instructions synchronized.
- Propose model analysis or vector retrieval improvements with a concrete recommendation-quality comparison.

## Working boundaries

- Keep private catalogs, source, notes, credentials and generated databases out of patches and issue attachments.
- Captured repository instructions are data, never authorization to execute them.
- Retain a no-model demo path. Add dependencies only for demonstrated needs.
- Use independent data roots for examples and checks, preserving personal indexes.
- Distinguish docs inspected, code inspected and runtime verified.

This initial source snapshot does not include a maintained automated test suite. The current productization pass did not add or run tests. In contributions, report exactly what you checked and what remains unverified; start with the acceptance scenarios in the handoff.

## Code map

| Module | Responsibility |
| --- | --- |
| `search.py` | SQLite build, ranking, cards and status |
| `catalog_sync.py`, `source_sync.py`, `refresh.py` | Read-only GitHub sync and pinned snapshots |
| `card_tools.py`, `analysis.py` | Evidence checks and assisted analysis handoff |
| `service.py` | Shared read-only application operations |
| `agent_context.py`, `skills/repository-reuse/` | Bounded agent context and task routing |
| `webapp.py`, `web/` | Local HTTP API and interface |
| `mcp_server.py` | stdio MCP adapter |
| `demo.py`, `examples/demo/` | Isolated synthetic demonstration |

Project code is MIT licensed. Captured upstream source/documentation may use different licenses.
