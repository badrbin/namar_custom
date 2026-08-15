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
        self.assertEqual(source.count("direction: rtl;"), 5)
        self.assertEqual(source.count("text-align: right;"), 3)
        self.assertNotIn("direction: ltr;", source)
        self.assertNotIn("text-align: left;", source)


if __name__ == "__main__":
    unittest.main()
