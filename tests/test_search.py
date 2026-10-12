from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import search


SHA_OLD = "a" * 40
SHA_CURRENT = "b" * 40


class SearchIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "data" / "cards").mkdir(parents=True)
        (self.root / "data" / "sources").mkdir(parents=True)
        (self.root / "schema.json").write_text(json.dumps({"database": "data/index.sqlite3"}), encoding="utf-8")
        self._write_catalog()
        self._write_snapshot(
            "acme/doc-assistant",
            SHA_CURRENT,
            "README.md",
            "# Local document assistant\n\nExtract text from PDF documents and create a local knowledge base.\n",
        )
        self._write_snapshot(
            "acme/ui-kit",
            SHA_CURRENT,
            "README.md",
            "# React UI components\n\nBuild reusable interface components quickly.\n",
        )
        self._write_snapshot(
            "acme/voice-clone",
            SHA_CURRENT,
            "README.md",
            "# Voice cloning\n\nConvert text to speech with a cloned voice.\n",
        )
        self._write_snapshot(
            "acme/git-clone",
            SHA_CURRENT,
            "README.md",
            "# Install\n\nRun git clone to download and install the project.\n",
        )
        self._write_card(
            "acme/doc-assistant",
            source_sha=SHA_OLD,
            kind="cli",
            evidence_level="source",
            summary="从 PDF 文档提取文字，建立本地知识库并支持检索问答。",
            feature="解析 PDF 文件并返回文本内容",
            evidence_path="src/parser.py",
            excerpt="def parse_pdf(path):\n    return extract_text(path)",
        )
        self._write_card(
            "acme/ui-kit",
            source_sha=SHA_CURRENT,
            kind="library",
            evidence_level="docs",
            summary="Reusable React UI components for building an interface.",
            feature="提供可复用的 React 界面组件",
            evidence_path="README.md",
            excerpt="Build reusable interface components quickly.",
        )
        self._write_card(
            "acme/voice-clone",
            source_sha=SHA_CURRENT,
            kind="app",
            evidence_level="readme",
            summary="Voice cloning tool that converts text to speech with a cloned voice.",
            feature="Generate speech in a cloned voice",
            evidence_path="README.md",
            excerpt="Convert text to speech with a cloned voice.",
        )
        self.db = self.root / "data" / "index.sqlite3"
        search.build_index(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_catalog(self) -> None:
        repositories = [
            {
                "full_name": "acme/doc-assistant",
                "url": "https://github.com/acme/doc-assistant",
                "description": "PDF parser and local document search tool",
                "language": "Python",
                "topics": ["pdf", "rag", "search"],
                "archived": False,
                "license": "MIT",
            },
            {
                "full_name": "acme/ui-kit",
                "url": "https://github.com/acme/ui-kit",
                "description": "Reusable React UI components",
                "language": "TypeScript",
                "topics": ["react", "components", "ui"],
                "archived": True,
                "private": True,
                "visibility": "private",
                "license": "MIT",
            },
            {
                "full_name": "acme/basic",
                "url": "https://github.com/acme/basic",
                "description": "A small parser utility",
                "language": "Go",
                "topics": ["parser"],
                "archived": False,
                "license": None,
            },
            {
                "full_name": "acme/voice-clone",
                "url": "https://github.com/acme/voice-clone",
                "description": "Voice cloning and text-to-speech app",
                "language": "Python",
                "topics": ["voice-cloning", "tts"],
                "archived": False,
                "license": "MIT",
            },
            {
                "full_name": "acme/git-clone",
                "url": "https://github.com/acme/git-clone",
                "description": "Clone a repository from GitHub",
                "language": "Shell",
                "topics": ["git"],
                "archived": False,
                "license": "MIT",
            },
        ]
        (self.root / "data" / "catalog.json").write_text(
            json.dumps({"user": "tester", "count": len(repositories), "complete": True, "repositories": repositories}, ensure_ascii=False),
            encoding="utf-8",
        )

    def _write_snapshot(self, full_name: str, sha: str, readme_path: str, content: str) -> None:
        slug = full_name.replace("/", "__")
        relative = f"data/sources/{slug}/snapshots/{sha}/files/{readme_path}"
        file_path = self.root / relative
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        source_dir = self.root / "data" / "sources" / slug
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "manifest.json").write_text(
            json.dumps({
                "full_name": full_name,
                "source_sha": sha,
                "fetched_at": "2026-10-11T00:00:00Z",
                "readme_path": readme_path,
                "files": [{"path": readme_path, "local_path": relative, "status": "ok", "sha256": "0" * 64}],
                "errors": [],
            }),
            encoding="utf-8",
        )

    def _write_card(
        self,
        full_name: str,
        *,
        source_sha: str,
        kind: str,
        evidence_level: str,
        summary: str,
        feature: str,
        evidence_path: str,
        excerpt: str,
    ) -> None:
        slug = full_name.replace("/", "__")
        manifest = json.loads((self.root / "data" / "sources" / slug / "manifest.json").read_text(encoding="utf-8"))
        file_item = next((item for item in manifest["files"] if item["path"] == evidence_path), None)
        local_path = file_item["local_path"] if file_item else None
        start_line = 4
        end_line = 5
        if local_path:
            lines = (self.root / local_path).read_text(encoding="utf-8").splitlines()
            try:
                start_line = next(index for index, line in enumerate(lines, 1) if line == excerpt)
                end_line = start_line
            except StopIteration:
                pass
        url = search.evidence_url(full_name, source_sha, evidence_path, start_line, end_line)
        card = {
            "schema_version": 1,
            "full_name": full_name,
            "kind": kind,
            "summary_zh": summary,
            "use_cases": [summary],
            "tags": ["voice cloning", "tts"] if "voice" in full_name else (["react", "components", "ui"] if "ui-kit" in full_name else ["pdf", "search", "rag", "文档"]),
            "features": [{"name": feature, "description": feature, "evidence": ["ev1"], "evidence_level": evidence_level}],
            "interfaces": ["CLI"],
            "requirements": [],
            "limitations": [],
            "comparisons": [],
            "evidence_level": evidence_level,
            "runtime_verified": False,
            "source_sha": source_sha,
            "analyzed_at": "2026-10-11T00:00:00Z",
            "evidence": [{
                "id": "ev1",
                "path": evidence_path,
                "start_line": start_line,
                "end_line": end_line,
                "excerpt": excerpt,
                "claim": feature,
                "url": url,
                "local_path": local_path,
            }],
            "analysis_scope": [evidence_path],
            "unverified": [],
            "analysis_version": 1,
        }
        (self.root / "data" / "cards" / f"{slug}.json").write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")

    def test_chinese_query_retrieves_card_and_marks_stale_commit(self) -> None:
        result = search.search_repositories("本地知识库问答", root=self.root)
        names = [item["full_name"] for item in result["results"]]
        self.assertIn("acme/doc-assistant", names)
        item = next(item for item in result["results"] if item["full_name"] == "acme/doc-assistant")
        self.assertTrue(item["card_stale"])
        self.assertIn(item["match"]["type"], {"metadata", "source_doc"})
        self.assertFalse(item["evidence"], "stale card evidence must not appear as current search evidence")
        shown = search.show_repo("acme/doc-assistant", root=self.root)
        self.assertEqual(shown["card_status"], "stale")
        self.assertIn(f"/blob/{SHA_OLD}/src/parser.py#L4-L5", shown["card"]["evidence"][0]["url"])
        stale_only = search.search_repositories("parse_pdf extract_text", root=self.root)
        self.assertNotIn("acme/doc-assistant", [entry["full_name"] for entry in stale_only["results"]])

    def test_english_query_retrieves_readme_with_pinned_line_evidence(self) -> None:
        result = search.search_repositories("extract text from PDF", root=self.root)
        item = next(item for item in result["results"] if item["full_name"] == "acme/doc-assistant")
        self.assertEqual(item["match"]["type"], "source_doc")
        self.assertEqual(item["match"]["evidence_level"], "readme")
        self.assertEqual(item["match"]["path"], "README.md")
        self.assertIn(f"/blob/{SHA_CURRENT}/README.md#L3-L3", item["match"]["url"])
        self.assertLessEqual(len(item["match"]["excerpt"]), 340)

    def test_metadata_filters_apply_to_search_results(self) -> None:
        archived = search.search_repositories("React components", root=self.root, archived=True)
        self.assertEqual([item["full_name"] for item in archived["results"]], ["acme/ui-kit"])
        not_archived = search.search_repositories("React components", root=self.root, archived=False)
        self.assertNotIn("acme/ui-kit", [item["full_name"] for item in not_archived["results"]])
        by_level = search.search_repositories("parse_pdf", root=self.root, evidence_level="source")
        self.assertEqual(by_level["results"], [], "a stale source-level card cannot make current results look source-verified")
        by_language = search.search_repositories("React components", root=self.root, language="typescript")
        self.assertEqual([item["full_name"] for item in by_language["results"]], ["acme/ui-kit"])

    def test_unsafe_evidence_path_is_returned_without_link(self) -> None:
        self._write_card(
            "acme/doc-assistant",
            source_sha=SHA_CURRENT,
            kind="cli",
            evidence_level="source",
            summary="从 PDF 文档提取文字并支持检索问答。",
            feature="读取 PDF 并解析内容",
            evidence_path="../secrets.txt",
            excerpt="unsafe path must never become an evidence link",
        )
        search.build_index(self.root)
        shown = search.show_repo("acme/doc-assistant", root=self.root)
        evidence = shown["card"]["evidence"][0]
        self.assertFalse(evidence["path_safe"])
        self.assertIsNone(evidence["url"])
        result = search.search_repositories("PDF parse", root=self.root)
        item = next(item for item in result["results"] if item["full_name"] == "acme/doc-assistant")
        self.assertFalse(item["card_ready"])
        self.assertNotIn(item["match"]["type"], {"card", "feature", "evidence"})
        self.assertFalse(item["evidence"], "unfinalized evidence must not be surfaced as verified support")

    def test_incomplete_current_card_is_not_indexed_as_functionality(self) -> None:
        card_path = self.root / "data" / "cards" / "acme__ui-kit.json"
        card = json.loads(card_path.read_text(encoding="utf-8"))
        card.pop("analysis_scope")
        card_path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
        search.build_index(self.root)

        result = search.search_repositories("React components", root=self.root)
        item = next(entry for entry in result["results"] if entry["full_name"] == "acme/ui-kit")
        self.assertFalse(item["card_ready"])
        self.assertIsNone(item["kind"])
        self.assertEqual(item["evidence_level"], "metadata")
        self.assertEqual(item["match"]["type"], "source_doc")
        shown = search.show_repo("acme/ui-kit", root=self.root)
        self.assertEqual(shown["card_status"], "unfinalized")

    def test_concept_coverage_ranks_voice_cloning_above_git_clone_boilerplate(self) -> None:
        result = search.search_repositories("声音克隆", root=self.root)
        names = [item["full_name"] for item in result["results"]]
        self.assertEqual(names[0], "acme/voice-clone")
        voice = result["results"][0]
        self.assertEqual(voice["lexical_coverage"]["matched_groups"], 2)
        clone_only = next(item for item in result["results"] if item["full_name"] == "acme/git-clone")
        self.assertLess(clone_only["lexical_coverage"]["matched_groups"], 2)
        self.assertIn("声音", clone_only["lexical_coverage"]["missing"])

    def test_readme_snippets_ignore_terms_hidden_in_badge_markup(self) -> None:
        content = (
            '<a href="https://example.com/voice-cloning"><img alt="voice clone badge" src="badge.svg"></a>\n'
            "\n"
            "Fish Speech supports zero-shot voice cloning from prompt audio.\n"
        )
        terms, groups = search.query_plan("声音克隆")
        snippet = search._query_excerpt(content, terms, 1, groups, strip_markup=True)
        self.assertEqual(snippet["start_line"], 3)
        self.assertIn("zero-shot voice cloning", snippet["excerpt"])
        self.assertNotIn("<a href", snippet["excerpt"])

    def test_results_are_deterministic_and_stats_report_catalog_coverage(self) -> None:
        first = search.search_repositories("parser", root=self.root)
        second = search.search_repositories("parser", root=self.root)
        self.assertEqual(first, second)
        self.assertEqual([item["full_name"] for item in first["results"]], ["acme/basic", "acme/doc-assistant"])
        summary = search.stats(self.root)
        self.assertEqual(summary["repositories"], 5)
        self.assertEqual(summary["catalog_expected_count"], 5)
        self.assertTrue(summary["catalog_complete"])
        self.assertEqual(summary["stale_cards"], 1)
        self.assertEqual(summary["ready_cards"], 2)
        self.assertEqual(summary["unfinalized_cards"], 0)
        shown_private = search.show_repo("acme/ui-kit", root=self.root)
        self.assertTrue(shown_private["repository"]["private"])
        self.assertEqual(shown_private["repository"]["visibility"], "private")
        self.assertEqual(summary["private"], 1)

    def test_unknown_chinese_phrase_recalls_instead_of_returning_nothing(self) -> None:
        # Regression: an unmatched multi-character Chinese phrase used to become
        # one conjunctive group demanding every overlapping bigram at once, so a
        # query such as this matched zero documents and returned no candidates at
        # all even though the corpus contained relevant repositories.
        result = search.search_repositories("本地向量数据库语义检索方案", root=self.root)
        self.assertGreater(len(result["results"]), 0)
        names = [item["full_name"] for item in result["results"]]
        self.assertIn("acme/doc-assistant", names)

    def test_natural_language_task_attributes_features_beyond_primary_hit(self) -> None:
        # Regression: feature attribution used to require the feature document to
        # rank first. A natural-language task ranks the card above the feature,
        # which silently produced zero matched features for most candidates.
        from service import IndexService  # noqa: PLC0415 - optional service layer

        service = IndexService(root=self.root)
        result = service.search("可复用的 React 界面组件库", limit=5)
        item = next(
            entry for entry in result["results"] if entry["full_name"] == "acme/ui-kit"
        )
        brief = item["selection_brief"]
        self.assertEqual(brief["candidate_state"], "feature_evidence_match")
        self.assertTrue(brief["matched_features"])
        names = [feature["name"] for feature in brief["matched_features"]]
        self.assertIn("提供可复用的 React 界面组件", names)

    def test_feature_attribution_excludes_repositories_that_only_matched_card(self) -> None:
        # A repository whose card or README surfaces must not report an unrelated
        # feature merely because repository-level evidence was loaded as context.
        from service import IndexService  # noqa: PLC0415 - optional service layer

        service = IndexService(root=self.root)
        result = service.search("语音克隆生成语音", limit=5)
        doc = next(
            (entry for entry in result["results"] if entry["full_name"] == "acme/doc-assistant"),
            None,
        )
        # doc-assistant may not appear at all; when it does, its PDF feature must
        # not be attributed to a query about voice cloning.
        if doc is not None:
            feature_names = [feature["name"] for feature in doc["selection_brief"]["matched_features"]]
            self.assertNotIn("解析 PDF 文件并返回文本内容", feature_names)

    def test_license_is_exposed_on_candidates_and_details(self) -> None:
        # License is a reuse hard constraint (MIT vs AGPL vs unknown), so it must
        # reach the agent alongside the candidate rather than staying catalog-only.
        result = search.search_repositories("React components", root=self.root)
        item = next(entry for entry in result["results"] if entry["full_name"] == "acme/ui-kit")
        self.assertEqual(item["license"], "MIT")
        shown = search.show_repo("acme/ui-kit", root=self.root)
        self.assertEqual(shown["repository"]["license"], "MIT")
        # A repository whose catalog record has no license reports null, which
        # means unknown rather than permissive.
        basic = search.show_repo("acme/basic", root=self.root)
        self.assertIsNone(basic["repository"]["license"])

    def test_failed_rebuild_preserves_previous_database(self) -> None:
        before = self.db.read_bytes()
        (self.root / "data" / "catalog.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(search.IndexErrorBase):
            search.build_index(self.root)
        self.assertEqual(self.db.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
