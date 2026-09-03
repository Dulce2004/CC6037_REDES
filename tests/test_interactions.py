"""Pruebas del repositorio de interacciones simuladas."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SOURCE_DIRECTORY = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.pharmacy import (  # noqa: E402
    AllergyRule,
    InteractionLookupError,
    InteractionQueryError,
    InteractionRepository,
    InteractionValidationError,
    MedicationInteractionRule,
    load_default_catalog,
    load_default_interactions,
)


class InteractionRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_default_catalog()
        self.repository = load_default_interactions(self.catalog)

    def test_controlled_rules_reference_catalog_and_cover_allergies(self) -> None:
        catalog_skus = {
            medication.sku for medication in self.catalog.list_medications()
        }

        self.assertGreaterEqual(len(self.repository.list_medication_rules()), 1)
        self.assertEqual(
            {rule.sku for rule in self.repository.list_allergy_rules()},
            catalog_skus,
        )

    def test_collections_are_returned_as_immutable_tuples(self) -> None:
        self.assertIsInstance(self.repository.list_medication_rules(), tuple)
        self.assertIsInstance(self.repository.list_allergy_rules(), tuple)

    def test_detects_controlled_medication_pair(self) -> None:
        alerts = self.repository.check_interactions(
            "MED-ANA-002",
            ["MED-GAS-001"],
            [],
        )

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].alert_type, "medication_interaction")
        self.assertEqual(alerts[0].related_sku, "MED-GAS-001")
        self.assertEqual(alerts[0].severity, "moderate")

    def test_pair_lookup_is_independent_of_requested_side(self) -> None:
        alerts = self.repository.check_interactions(
            "MED-GAS-001",
            ["MED-ANA-002"],
            [],
        )

        self.assertEqual(alerts[0].related_sku, "MED-ANA-002")

    def test_detects_accent_insensitive_allergy_term(self) -> None:
        alerts = self.repository.check_interactions(
            "MED-RX-001",
            [],
            ["Alergia grave a penicilínas"],
        )

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].alert_type, "allergy_alert")
        self.assertEqual(alerts[0].severity, "high")

    def test_no_controlled_match_returns_empty_tuple(self) -> None:
        alerts = self.repository.check_interactions(
            "MED-ANT-001",
            ["MED-GAS-002"],
            ["polen"],
        )

        self.assertEqual(alerts, ())

    def test_unknown_requested_or_current_sku_is_lookup_error(self) -> None:
        cases = (
            ("MED-MISSING", [], []),
            ("MED-ANA-001", ["MED-MISSING"], []),
        )

        for requested, current, allergies in cases:
            with self.subTest(requested=requested, current=current):
                with self.assertRaises(InteractionLookupError):
                    self.repository.check_interactions(
                        requested,
                        current,
                        allergies,
                    )

    def test_rejects_invalid_query_shapes(self) -> None:
        cases = (
            (None, [], []),
            ("bad sku!", [], []),
            ("MED-ANA-001", None, []),
            ("MED-ANA-001", [], None),
            ("MED-ANA-001", ["MED-GAS-001", "MED-GAS-001"], []),
            ("MED-ANA-001", [], [""]),
        )

        for requested, current, allergies in cases:
            with self.subTest(
                requested=requested,
                current=current,
                allergies=allergies,
            ):
                with self.assertRaises(InteractionQueryError):
                    self.repository.check_interactions(
                        requested,
                        current,
                        allergies,
                    )

    def test_rejects_requested_sku_repeated_as_current(self) -> None:
        with self.assertRaisesRegex(InteractionQueryError, "must not repeat"):
            self.repository.check_interactions(
                "MED-ANA-001",
                ["MED-ANA-001"],
                [],
            )

    def test_constructor_rejects_wrong_catalog_type(self) -> None:
        with self.assertRaises(InteractionValidationError):
            InteractionRepository(
                catalog=object(),
                medication_rules=(),
                allergy_rules=(),
            )

    def test_rule_models_reject_invalid_controlled_data(self) -> None:
        with self.assertRaises(InteractionValidationError):
            MedicationInteractionRule(
                skus=("MED-ANA-001", "MED-ANA-001"),
                severity="moderate",
                message="Simulated rule.",
            )
        with self.assertRaises(InteractionValidationError):
            MedicationInteractionRule(
                skus=("MED-ANA-001", "MED-GAS-001"),
                severity="safe",
                message="Simulated rule.",
            )
        with self.assertRaises(InteractionValidationError):
            AllergyRule(
                sku="MED-ANA-001",
                terms=("Paracetamol", "paracétamol"),
            )

    def test_repository_rejects_rule_with_unknown_catalog_sku(self) -> None:
        bad_rule = MedicationInteractionRule(
            skus=("MED-ANA-001", "MED-MISSING"),
            severity="moderate",
            message="Simulated rule.",
        )

        with self.assertRaisesRegex(
            InteractionValidationError,
            "Unknown interaction-rule SKU",
        ):
            InteractionRepository(
                catalog=self.catalog,
                medication_rules=(bad_rule,),
                allergy_rules=self.repository.list_allergy_rules(),
            )


if __name__ == "__main__":
    unittest.main()
