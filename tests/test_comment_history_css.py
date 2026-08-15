from pathlib import Path
import unittest


CSS_PATH = (
    Path(__file__).resolve().parents[1]
    / "namar_test"
    / "public"
    / "css"
    / "comment_history.bundle.css"
)


class CommentHistoryCssTest(unittest.TestCase):
    def test_rtlcss_keeps_the_explicit_arabic_direction(self):
        source = CSS_PATH.read_text(encoding="utf-8")

        self.assertTrue(source.lstrip().startswith("/*rtl:begin:ignore*/"))
        self.assertTrue(source.rstrip().endswith("/*rtl:end:ignore*/"))
        self.assertGreaterEqual(source.count("direction: rtl;"), 4)
        self.assertGreaterEqual(source.count("text-align: right;"), 3)
        self.assertNotIn("direction: ltr;", source)
        self.assertNotIn("text-align: left;", source)

    def test_drawer_is_responsive_and_replaces_the_old_inline_history(self):
        source = CSS_PATH.read_text(encoding="utf-8")

        self.assertIn("width: clamp(420px, 40vw, 640px);", source)
        self.assertIn("height: calc(100dvh - var(--navbar-height, 48px));", source)
        self.assertIn("height: min(82vh, 720px);", source)
        self.assertIn("height: min(82dvh, 720px);", source)
        self.assertIn("env(safe-area-inset-bottom, 0px)", source)
        self.assertIn("@media (prefers-reduced-motion: reduce)", source)
        self.assertIn(".namar-comment-history-trigger", source)
        self.assertIn(".namar-comment-history-drawer", source)
        self.assertIn(".namar-comment-history-snapshot::marker", source)
        self.assertNotIn(".namar-comment-history-changes", source)


if __name__ == "__main__":
    unittest.main()
