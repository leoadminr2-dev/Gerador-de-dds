"""Regression guard for the research endpoint's failure contract.

Keeps the user-visible online-research action from silently returning an
unverified generic draft after online search fails.
"""
from pathlib import Path
import ast
import unittest


class ResearchFailureContractTests(unittest.TestCase):
    def test_online_research_does_not_fallback_to_generic_draft(self):
        source = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        endpoint = next(
            node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "research_topic"
        )
        body = ast.get_source_segment(source, endpoint)
        self.assertIn("'mode': 'research-unavailable'", body)
        self.assertIn("503", body)
        self.assertNotIn("'mode':'offline-draft'", body)
        self.assertNotIn("'OFFLINE-DRAFT:'", body)


if __name__ == "__main__":
    unittest.main()
