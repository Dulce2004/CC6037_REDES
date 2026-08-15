"""Pruebas unitarias de la lógica independiente de farmacia."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SOURCE_DIRECTORY = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.pharmacy import (  # noqa: E402
    SymptomValidationError,
    classify_symptoms,
)


class SymptomClassifierTests(unittest.TestCase):
    def test_classifies_respiratory_case(self) -> None:
        result = classify_symptoms(["fever", "cough", "sore_throat"])

        self.assertEqual(result["status"], "classified")
        self.assertEqual(result["category"], "respiratory")

    def test_classifies_allergy_case(self) -> None:
        result = classify_symptoms(
            ["sneezing", "nasal_congestion", "itchy_eyes"]
        )

        self.assertEqual(result["category"], "allergy")

    def test_classifies_gastrointestinal_case(self) -> None:
        result = classify_symptoms(["nausea", "diarrhea", "abdominal_pain"])

        self.assertEqual(result["category"], "gastrointestinal")

    def test_returns_unclassified_when_no_rule_matches(self) -> None:
        result = classify_symptoms(["fever", "itchy_eyes"])

        self.assertEqual(result["status"], "unclassified")
        self.assertIsNone(result["category"])
        self.assertEqual(result["matchedSymptoms"], [])

    def test_rejects_empty_list(self) -> None:
        with self.assertRaisesRegex(SymptomValidationError, "must not be empty"):
            classify_symptoms([])

    def test_rejects_unknown_symptom(self) -> None:
        with self.assertRaisesRegex(SymptomValidationError, "magic_symptom"):
            classify_symptoms(["fever", "magic_symptom"])

    def test_rejects_incorrect_symptoms_type(self) -> None:
        with self.assertRaisesRegex(SymptomValidationError, "must be an array"):
            classify_symptoms("fever")

    def test_rejects_null_symptoms(self) -> None:
        with self.assertRaisesRegex(SymptomValidationError, "must be an array"):
            classify_symptoms(None)

    def test_rejects_non_string_element(self) -> None:
        with self.assertRaisesRegex(SymptomValidationError, "must be a string"):
            classify_symptoms(["fever", 123])

    def test_rejects_empty_string(self) -> None:
        with self.assertRaisesRegex(SymptomValidationError, "must not be empty"):
            classify_symptoms([""])

    def test_rejects_invalid_identifier_format(self) -> None:
        with self.assertRaisesRegex(SymptomValidationError, "format"):
            classify_symptoms(["sore throat"])

    def test_normalizes_case_and_whitespace(self) -> None:
        result = classify_symptoms([" Fever ", "COUGH"])

        self.assertEqual(result["category"], "respiratory")
        self.assertEqual(result["matchedSymptoms"], ["fever", "cough"])


if __name__ == "__main__":
    unittest.main()
