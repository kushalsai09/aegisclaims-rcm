import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.rag.resolver import ResolutionResult, resolve_denial
from app.rag.vector_store import PolicyMatch


POLICY_MATCH = PolicyMatch(
    text="Place the authorization number in CMS-1500 Field 23.",
    payer="Aetna",
    policy_title="Test Authorization Policy",
    section="Authorization Submission",
    source="test.txt",
    distance=0.1,
)


class PreciseResolverTests(unittest.TestCase):
    @patch("app.rag.resolver.search_policies", return_value=[POLICY_MATCH])
    def test_uses_exact_authorization_number(self, _search) -> None:
        result = resolve_denial(
            denial_code="CO-197",
            denial_reason="Authorization absent",
            cpt_code="12001",
            insurer="Aetna",
            auth_number_in_chart="PA-48102",
            diagnosis_code="S01.81XA",
        )

        self.assertEqual(
            result.suggested_fix,
            "Authorization number 'PA-48102' is documented but omitted from CPT 12001; "
            "enter 'PA-48102' in CMS-1500 Field 23 and submit a corrected claim.",
        )

    @patch("app.rag.resolver.search_policies", return_value=[POLICY_MATCH])
    def test_identifies_omitted_modifier(self, _search) -> None:
        result = resolve_denial(
            denial_code="CO-104",
            denial_reason="Service unbundled",
            cpt_code="99214",
            insurer="BCBS",
            modifier_attached=None,
            diagnosis_code="J06.9",
            eob_notes="The note supports a significant, separately identifiable E/M service.",
        )

        self.assertIn("Modifier -25 is omitted from CPT 99214", result.suggested_fix)
        self.assertNotIn("1.", result.suggested_fix)
        self.assertNotIn("\n", result.suggested_fix)

    def test_rejects_checklist_output(self) -> None:
        with self.assertRaises(ValidationError):
            ResolutionResult(
                suggested_fix="1. Check the chart 2. Update the claim",
                policy_citation="Policy, Section: excerpt",
            )


if __name__ == "__main__":
    unittest.main()
