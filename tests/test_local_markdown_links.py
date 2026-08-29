import re
import unittest
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


class LocalMarkdownLinkTests(unittest.TestCase):
    def test_local_markdown_links_exist(self):
        missing = []
        for document in ROOT.rglob("*.md"):
            if ".git" in document.parts:
                continue
            text = document.read_text(encoding="utf-8")
            for raw_target in LINK_PATTERN.findall(text):
                target = raw_target.strip().strip("<>").split("#", 1)[0]
                if not target or "://" in target or target.startswith(("mailto:", "data:")):
                    continue
                resolved = (document.parent / unquote(target)).resolve()
                if not resolved.exists():
                    missing.append(f"{document.relative_to(ROOT)} -> {raw_target}")

        self.assertEqual([], missing, "发现失效的本地 Markdown 链接")


if __name__ == "__main__":
    unittest.main()
