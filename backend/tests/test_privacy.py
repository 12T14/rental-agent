import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app.privacy as privacy


class PrivacyCleanupTests(unittest.TestCase):
    def test_platform_cleanup_removes_only_fixed_platform_state_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {platform: root / platform / "storage_state.json" for platform in privacy.PLATFORMS}
            for path in paths.values():
                path.parent.mkdir(parents=True)
                path.write_text("state", encoding="utf-8")
            unrelated = root / "unrelated.txt"
            unrelated.write_text("keep", encoding="utf-8")

            with patch.object(privacy, "_session_path", side_effect=paths.__getitem__):
                result = privacy.clear_platform_sessions()

            self.assertEqual(result["cleared_count"], 3)
            self.assertTrue(all(not path.exists() for path in paths.values()))
            self.assertTrue(unrelated.exists())

    def test_artifact_cleanup_keeps_root_and_removes_nested_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "artifacts"
            nested = root / "run-1"
            nested.mkdir(parents=True)
            (root / "page.html").write_text("html", encoding="utf-8")
            (nested / "result.json").write_text("{}", encoding="utf-8")

            result = privacy.clear_artifacts(root)

            self.assertEqual(result["removed_files"], 2)
            self.assertTrue(root.is_dir())
            self.assertEqual(list(root.iterdir()), [])
