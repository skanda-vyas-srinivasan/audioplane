"""Keep the canonical onboarding links and complete Python snippets usable."""

import ast
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).parents[3]
DOCS = (
    "README.md", "docs/getting-started.md", "docs/developer-workflows.md",
    "docs/developer-workflows-validation.md", "docs/ai-integration.md",
    "docs/runtime-documentation.md", "SDKs/python/README.md", "Examples/README.md",
    "Examples/audio-agent/README.md",
)


class OnboardingDocsTests(unittest.TestCase):
    def test_relative_document_links_exist(self):
        for name in DOCS:
            document = ROOT / name
            text = document.read_text()
            for target in re.findall(r"\]\(([^)]+)\)", text):
                if target.startswith(("http:", "https:", "mailto:", "#")):
                    continue
                target = target.split("#", 1)[0]
                with self.subTest(document=name, target=target):
                    self.assertTrue((document.parent / target).exists(), target)

    def test_canonical_complete_python_examples_parse(self):
        for name in ("docs/getting-started.md", "docs/developer-workflows.md", "README.md"):
            for snippet in re.findall(r"```python\n(.*?)\n```", (ROOT / name).read_text(), re.S):
                with self.subTest(document=name):
                    ast.parse(snippet, filename=name)

    def test_newcomer_guides_do_not_point_to_old_checkout(self):
        for name in DOCS:
            text = (ROOT / name).read_text()
            with self.subTest(document=name):
                self.assertNotIn("cd ~/Sonexis", text)
                self.assertNotIn("/absolute/path/to/Sonexis/", text)


if __name__ == "__main__":
    unittest.main()
