# GitHub Star Index

Give coding agents a grounded way to find and reuse repositories you have already collected. The index records what selected projects do, what they require, what remains unknown, and which fixed-commit source lines support each capability. During a development task, an agent can search for reusable components or implementations, inspect a few candidates, and read only the evidence needed to make a choice.

[中文说明](docs/README.zh-CN.md) · [Requirements](docs/REQUIREMENTS.md) · [Agent handoff](docs/HANDOFF.md) · [Development plan](docs/PLAN.md) · [Competitors](docs/COMPETITORS.md) · [Business and adoption](docs/BUSINESS_AND_ADOPTION.md)

## Connect an AI agent

The stdio MCP server exposes compact, task-oriented tools:

- `find_reusable_repositories`: find up to five candidates for a concrete task.
- `inspect_repository`: inspect selected features, prerequisites, limitations and card status.
- `read_repository_evidence`: retrieve a small set of fixed-commit evidence excerpts.
- `index_status`, `search_repositories`, `get_repository`, and `compare_repositories` remain available for general index operations.

Start it from an MCP-compatible client configuration, replacing the example path with the absolute path to this checkout:

```sh
python mcp_server.py
python mcp_server.py --root /path/to/my-index --public-only
```

The server defaults to the project directory as its index root. It reads the index and does not make model calls. For the agent to consider this knowledge during development, install the portable [repository-reuse skill](skills/repository-reuse/SKILL.md) in the target agent's skill directory. Skill routing is guidance; invocation and MCP tool availability depend on the client and its configuration. See [interface contracts](docs/INTERFACES.md) for tool arguments and result fields. Real-client interoperability has not yet been verified.

When MCP is unavailable but the index files are accessible, the bounded CLI offers the same progressive flow. Call the script from this checkout by its path; the current working directory may be another project:

```sh
python "/path/to/github-star-index/agent_context.py" --root "/path/to/my-index" find "Build a React page with reusable motion components" --terms animation component --limit 5
python "/path/to/github-star-index/agent_context.py" --root "/path/to/my-index" inspect owner/repo --features "Exact feature name"
python "/path/to/github-star-index/agent_context.py" --root "/path/to/my-index" evidence owner/repo --ids ev1 ev2
```

## Prepare your own index

Requires Python 3.10+ with SQLite FTS5. There are no additional Python dependencies. To sync the currently authenticated GitHub account:

```sh
gh auth login
python catalog_sync.py
python source_sync.py --all
python search.py build
```

Sync captures repository metadata, file trees and README text where available. It does not automatically produce deep capability cards. For a selected repository, capture decisive files and prepare a bounded packet for an AI assistant you already use:

```sh
python source_sync.py --repo owner/repo --paths src/main.py docs/usage.md
python analysis.py pack --repo owner/repo
```

Review the resulting card against the captured sources, then import it:

```sh
python analysis.py ingest draft.json --repo owner/repo
python search.py build
```

Existing cards require `--replace`; prior cards are archived. Import checks repository identity, commit, captured hashes, evidence lines and per-feature references, but cannot establish that every interpretation is semantically correct. See [analysis guidance](ANALYSIS_GUIDE.md).

`python refresh.py` updates the catalog and snapshots; cards from changed commits become stale and require review. For another data root, copy `schema.json` there and pass the same `--root /path/to/my-index` to sync, analysis, indexing and serving commands.

## Local preview and maintenance

The Web interface is a local way to inspect index coverage, search behavior, evidence and candidate details. It is secondary to the agent workflow; it is not a Stars manager or an AI recommendation service.

```sh
python demo.py
python webapp.py --demo
```

Open http://127.0.0.1:8765. The demo uses four fictional repositories and synthetic evidence in an isolated `data/demo/` directory. For a personal index, run `python webapp.py`.

## Retrieval and evidence limits

Search uses bilingual SQLite FTS5 lexical matching without embeddings or model reranking. Candidate order is not a suitability score. Missing terms mean unknown, not unsupported. Source-level analysis samples representative paths; it is not a complete audit or runtime verification. A citation matching captured lines does not prove that the interpretation is correct.

## Privacy and contribution

Personal catalogs, source snapshots, cards, databases and generated indexes stay local in ignored paths. The local HTTP server binds to `127.0.0.1`; `--public-only` excludes private and visibility-unknown entries before ranking and pagination. This is not internet hosting or authentication. Captured third-party text keeps its own rights; it is not covered by this project's [MIT license](LICENSE). See [CONTRIBUTING.md](CONTRIBUTING.md).

This preview has not completed browser acceptance, real-client MCP interoperability, cross-platform installation checks, or comparative recommendation-quality evaluation. A small unit suite lives in `tests/` (`python -m pytest tests/`), covering index building/search and pinned-snapshot capture; it does not cover the MCP adapter, the Web interface or recommendation quality.
