"""Regression tests for unified diff records and line counts."""
import io
import unittest

from rich.console import Console
from utils.diff import compute_diff, diff_stats, render_diff


class DiffTests(unittest.TestCase):
    def test_single_line_replacement_has_separate_records(self):
        diff = compute_diff("a\n", "b\n", "x")
        self.assertEqual(diff, "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n")
        self.assertEqual(diff_stats(diff), (1, 1))

    def test_additions_deletions_and_empty_files(self):
        for old, new, expected in [
            ("", "a\nb\n", (2, 0)),
            ("a\nb\n", "", (0, 2)),
            ("a\n", "a\nb\n", (1, 0)),
            ("a\nb\n", "a\n", (0, 1)),
            ("", "", (0, 0)),
            ("same", "same", (0, 0)),
        ]:
            with self.subTest(old=old, new=new):
                self.assertEqual(diff_stats(compute_diff(old, new, "x")), expected)

    def test_missing_trailing_newlines_are_marked(self):
        diff = compute_diff("a", "b", "x")
        self.assertIn("-a\n\\ No newline at end of file\n+b\n", diff)
        self.assertEqual(diff.count("\\ No newline at end of file"), 2)
        self.assertEqual(diff_stats(diff), (1, 1))

    def test_trailing_newline_only_changes_are_visible(self):
        for old, new in [("a", "a\n"), ("a\n", "a")]:
            with self.subTest(old=old):
                diff = compute_diff(old, new, "x")
                self.assertEqual(diff.count("\\ No newline at end of file"), 1)
                self.assertEqual(diff_stats(diff), (1, 1))

    def test_source_lines_starting_with_header_markers_are_counted(self):
        diff = compute_diff("--old\n", "++new\n", "x")
        self.assertEqual(diff_stats(diff), (1, 1))

    def test_multiple_hunks(self):
        lines = [f"line {i}\n" for i in range(30)]
        modified = lines.copy()
        modified[0] = "first changed\n"
        modified[-1] = "last changed\n"
        diff = compute_diff("".join(lines), "".join(modified), "x")
        self.assertEqual(sum(line.startswith("@@ ") for line in diff.splitlines()), 2)
        self.assertEqual(diff_stats(diff), (2, 2))

    def test_preview_uses_full_stats_even_when_truncated(self):
        diff = compute_diff("", "a\n" * 100, "x")
        output = io.StringIO()
        render_diff(diff, "x", Console(file=output, width=100), max_lines=10)
        text = output.getvalue()
        self.assertIn("+100 -0", text)
        self.assertIn("more lines", text)
        self.assertLess(len(text.splitlines()), 20)


if __name__ == "__main__":
    unittest.main()
