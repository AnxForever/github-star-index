# Changelog

## 0.1.0 preview — unreleased

- Retrieval quality: unknown multi-character Chinese phrases no longer collapse into one conjunctive concept group, which previously made queries such as “向量数据库检索” return zero candidates.
- Retrieval quality: broader bilingual concept coverage (vector database, transcription, speech synthesis, crawler, database, chat, deploy, prompt, fine-tune, evaluation), so operational terms map to real repositories instead of noise bigrams.
- Feature attribution: `matched_features` now fires whenever a feature's own indexed text matched the query, independent of which document ranked first, instead of requiring the feature to be the primary hit. Attribution stays strict, so repository-level context cannot light up unrelated features.
- Agent-first workflow: compact candidate discovery, feature inspection and evidence retrieval through MCP and CLI.
- Portable repository-reuse Skill for component, dependency and implementation choices during development.
- Requirements and developer handoff; Web reframed as integration guidance and retrieval diagnostics.
- Local task search, filters, capability details, pinned evidence and candidate comparison.
- Shared read-only application API and stdio MCP tools.
- Account-independent GitHub sync and configurable index roots.
- Isolated synthetic demo with no model or GitHub login requirement.
- Numbered source analysis packets, evidence-checked card import and archived replacements.
- Unit tests for index building/search and pinned-snapshot capture (`tests/`, `python -m pytest tests/`).
- Candidate and detail results now expose the catalog `license` (SPDX id) so reuse constraints like MIT vs AGPL are visible to the agent.
- Local retrieval evaluator `eval_retrieval.py` scoring recall@k, precision@k, MRR, empty results and feature coverage against a labeled task set.
- Chinese and English onboarding, competitor research and development plan.

Source review and local startup are recorded separately in `docs/IMPLEMENTATION_STATUS.md`. This is a source preview, not a tagged release. This productization pass has not run browser acceptance, MCP interoperability or recommendation-quality comparisons.
