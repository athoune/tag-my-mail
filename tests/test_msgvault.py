"""Unit tests for msgvault stderr error extraction (no subprocess here)."""

import unittest

from tag_my_mail.msgvault import _extract_error

MSgVAULT_STDERR = """\
time=2026-11-13T10:57:51.017+01:00 level=INFO msg="msgvault startup" run_id=93233216c035 command="msgvault query"
Error: query: execute query: Catalog Error: Table with name vue_inexistante does not exist!
Did you mean "relationship_unsent_messages"?

LINE 1: SELECT * FROM vue_inexistante
                      ^^^^^^^^^^^^^^^
time=2026-11-13T10:57:51.037+01:00 level=INFO msg="msgvault exit" outcome=error
"""


class ExtractErrorTests(unittest.TestCase):
    def test_strips_logs_and_carets(self):
        out = _extract_error(MSgVAULT_STDERR, 1)
        self.assertIn("Catalog Error: Table with name vue_inexistante does not exist!", out)
        self.assertIn('Did you mean "relationship_unsent_messages"?', out)
        self.assertNotIn("time=", out)
        self.assertNotIn("^^^", out)
        self.assertNotIn("LINE 1", out)

    def test_falls_back_to_exit_code(self):
        self.assertEqual(_extract_error("", 3), "exit code 3")
        self.assertEqual(_extract_error(None, 3), "exit code 3")
        self.assertEqual(_extract_error("time=only logs\n", 3), "exit code 3")


if __name__ == "__main__":
    unittest.main()
