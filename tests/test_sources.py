import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import source_sync


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="star-index-source-test-")
        self.root = Path(self.temp.name).resolve()
        self.sources = self.root / "data" / "sources"
        self.patches = [patch.object(source_sync, "ROOT", self.root), patch.object(source_sync, "SOURCES", self.sources)]
        for p in self.patches:
            p.start()
        self.repo = {"full_name": "owner/repo", "default_branch": "main", "url": "https://github.com/owner/repo"}
        self.old_sha, self.new_sha = "a" * 40, "b" * 40

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        # A fresh tempfile directory owned by this test, never user content.
        self.temp.cleanup()

    def seed_previous(self):
        folder = self.sources / "owner__repo"
        snapshot = folder / "snapshots" / self.old_sha
        snapshot.mkdir(parents=True)
        obj = {"path": "README.md", "type": "file", "encoding": "base64", "size": 4, "content": base64.b64encode(b"old\n").decode()}
        item = source_sync.store_file("owner/repo", self.old_sha, obj, snapshot)
        old = {"full_name": "owner/repo", "source_sha": self.old_sha, "files": [item], "capture_complete": True, "errors": [], "readme_path": "README.md"}
        source_sync.atomic_json(folder / "manifest.json", old)
        return folder, item

    def test_unsafe_remote_path_is_rejected(self):
        for value in ("../secret", "/absolute", "C:/secret", "folder\\file", "a/../../b"):
            with self.subTest(path=value), self.assertRaises(ValueError):
                source_sync.safe_repo_path(value)
        self.assertEqual(str(source_sync.safe_repo_path("src/main.py")), "src/main.py")

    def test_unchanged_refresh_reuses_existing_evidence(self):
        folder, item = self.seed_previous()
        with patch.object(source_sync, "api", return_value={"object": {"sha": self.old_sha}}) as api:
            result = source_sync.capture(self.repo, refresh=True)
        self.assertEqual(api.call_count, 1)
        self.assertEqual(result["status"], "unchanged")
        self.assertEqual((self.root / item["local_path"]).read_text(), "old\n")
        current = json.loads((folder / "manifest.json").read_text())
        self.assertEqual(current["files"], [item])

    def test_new_commit_keeps_old_snapshot_and_replaces_current_file_list(self):
        folder, old_item = self.seed_previous()
        responses = [{"object": {"sha": self.new_sha}}, {"tree": [], "truncated": False},
                     {"path": "README.md", "type": "file", "encoding": "base64", "size": 4, "content": base64.b64encode(b"new\n").decode()}]
        with patch.object(source_sync, "api", side_effect=responses):
            source_sync.capture(self.repo, refresh=True)
        current = json.loads((folder / "manifest.json").read_text())
        self.assertEqual(current["source_sha"], self.new_sha)
        self.assertTrue(all(f["source_sha"] == self.new_sha for f in current["files"]))
        self.assertEqual((self.root / old_item["local_path"]).read_text(), "old\n")
        self.assertEqual((self.root / current["files"][0]["local_path"]).read_text(), "new\n")

    def test_failed_new_tree_read_preserves_current_manifest(self):
        folder, _ = self.seed_previous()
        previous = (folder / "manifest.json").read_bytes()
        with patch.object(source_sync, "api", side_effect=[{"object": {"sha": self.new_sha}}, RuntimeError("network failed")]):
            with self.assertRaises(RuntimeError):
                source_sync.capture(self.repo, refresh=True)
        self.assertEqual((folder / "manifest.json").read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()
