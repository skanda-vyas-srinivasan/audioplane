"""Keep SDK prerelease metadata honest and compatible with the Runtime base."""

import importlib.util
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[3]
SPEC = importlib.util.spec_from_file_location(
    "runtime_version_check", ROOT / "Scripts/check-runtime-version.py")
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)
VERSION_FILES = (
    "Sources/SonexisRuntime/IPC/RuntimeProtocol.swift",
    "Distribution/Runtime-Embedded-Info.plist",
    "SDKs/python/pyproject.toml", "SDKs/python/setup.py",
    "SDKs/python/src/sonexis/client.py",
    "SDKs/python/src/sonexis/mcp_server.py",
    "SDKs/python/src/sonexis/__init__.py",
    "SDKs/typescript/src/index.ts", "SDKs/typescript/package.json",
    "SDKs/typescript/package-lock.json",
)


class ReleaseMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in VERSION_FILES:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)

    def edit(self, name, before, after):
        target = self.root / name
        text = target.read_text()
        self.assertIn(before, text)
        target.write_text(text.replace(before, after))

    def check(self):
        with patch.object(CHECK, "ROOT", self.root):
            CHECK.main()

    def test_current_candidate_matches_runtime_base(self):
        self.check()

    def test_stable_release_requires_stable_metadata(self):
        for name in VERSION_FILES:
            if name.startswith("SDKs/python/"):
                self.edit(name, "1.0.0rc1", "1.0.0")
        with self.assertRaisesRegex(AssertionError, "classifier"):
            self.check()
        for name in ("SDKs/python/pyproject.toml", "SDKs/python/setup.py"):
            self.edit(name, "Development Status :: 4 - Beta",
                      "Development Status :: 5 - Production/Stable")
        self.check()

    def test_candidate_cannot_claim_production_stability(self):
        self.edit("SDKs/python/pyproject.toml", "Development Status :: 4 - Beta",
                  "Development Status :: 5 - Production/Stable")
        with self.assertRaisesRegex(AssertionError, "classifier"):
            self.check()

    def test_wrong_base_or_module_version_rejected(self):
        self.edit("SDKs/python/pyproject.toml", 'version = "1.0.0rc1"',
                  'version = "2.0.0rc1"')
        with self.assertRaisesRegex(AssertionError, "match Runtime base"):
            self.check()
        self.edit("SDKs/python/pyproject.toml", 'version = "2.0.0rc1"',
                  'version = "1.0.0rc1"')
        self.edit("SDKs/python/src/sonexis/__init__.py", "1.0.0rc1", "1.0.0")
        with self.assertRaisesRegex(AssertionError, "expected 1.0.0rc1"):
            self.check()

    def test_package_readme_has_no_checkout_relative_links(self):
        text = (ROOT / "SDKs/python/README.md").read_text()
        for target in re.findall(r"\]\(([^)]+)\)", text):
            self.assertTrue(target.startswith(("https://", "#", "mailto:")), target)
        self.assertIn("does not install the Runtime", text)


if __name__ == "__main__":
    unittest.main()
