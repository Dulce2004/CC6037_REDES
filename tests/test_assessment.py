"""Pruebas del evaluador educativo de síntomas en texto natural."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SOURCE_DIRECTORY = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.pharmacy import (  # noqa: E402
    ASSESSMENT_DISCLAIMER,
    SymptomAssessmentValidationError,
    assess_symptoms,
)


class SymptomAssessmentTests(unittest.TestCase):
    def test_assesses_spanish_natural_language_with_existing_rules(self) -> None:
        result = assess_symptoms(
            "Tengo fiebre y dolor de garganta desde ayer",
            age=24,
            duration_days=1,
        )

        self.assertEqual(result["severity"], "mild")
        self.assertEqual(result["category"], "respiratory")
        self.assertEqual(
            result["recognized_symptoms"],
            ["fever", "sore_throat"],
        )
        self.assertEqual(result["red_flags"], [])

    def test_normalizes_accents_case_and_punctuation(self) -> None:
        result = assess_symptoms("¡NÁUSEAS, diarrea y dolor de estómago!")

        self.assertEqual(result["category"], "gastrointestinal")
        self.assertEqual(
            result["recognized_symptoms"],
            ["nausea", "diarrhea", "abdominal_pain"],
        )

    def test_urgent_red_flag_takes_priority_over_category(self) -> None:
        result = assess_symptoms(
            "Tengo tos y fiebre, además no puedo respirar",
            age=24,
            duration_days=1,
        )

        self.assertEqual(result["severity"], "urgent")
        self.assertEqual(result["category"], "respiratory")
        self.assertIn("difficulty_breathing", result["red_flags"])
        self.assertIn("Seek urgent medical care", result["recommended_action"])
        self.assertFalse(result["medication_purchase_recommended"])

    def test_infant_age_with_fever_is_urgent(self) -> None:
        result = assess_symptoms("Tiene fiebre", age=0, duration_days=1)

        self.assertEqual(result["severity"], "urgent")
        self.assertIn("infant_age_with_fever", result["reasons"])

    def test_long_duration_is_moderate(self) -> None:
        result = assess_symptoms(
            "Tengo estornudos y congestión nasal",
            age=30,
            duration_days=7,
        )

        self.assertEqual(result["severity"], "moderate")
        self.assertIn(
            "symptoms_present_for_seven_or_more_days",
            result["reasons"],
        )

    def test_unrecognized_text_is_moderate_not_a_diagnosis(self) -> None:
        result = assess_symptoms("Me siento diferente desde ayer")

        self.assertEqual(result["severity"], "moderate")
        self.assertIsNone(result["category"])
        self.assertEqual(result["recognized_symptoms"], [])
        self.assertEqual(result["disclaimer"], ASSESSMENT_DISCLAIMER)
        self.assertFalse(result["medication_purchase_recommended"])

    def test_rejects_invalid_symptom_text(self) -> None:
        cases = (None, ["fever"], "", "   ", "!!!", "x" * 1001)

        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(SymptomAssessmentValidationError):
                    assess_symptoms(value)

    def test_rejects_invalid_age(self) -> None:
        for value in (-1, 121, True, 24.5, "24"):
            with self.subTest(value=value):
                with self.assertRaises(SymptomAssessmentValidationError):
                    assess_symptoms("Tengo tos", age=value)

    def test_rejects_invalid_duration(self) -> None:
        for value in (-1, 366, False, 1.5, "1"):
            with self.subTest(value=value):
                with self.assertRaises(SymptomAssessmentValidationError):
                    assess_symptoms("Tengo tos", duration_days=value)


if __name__ == "__main__":
    unittest.main()
