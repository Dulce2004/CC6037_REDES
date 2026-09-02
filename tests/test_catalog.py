"""Pruebas de integridad para el catálogo simulado de farmacia."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

SOURCE_DIRECTORY = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.pharmacy import (  # noqa: E402
    CATALOG_CURRENCY,
    EXPECTED_BRANCH_IDS,
    Branch,
    CatalogQueryError,
    CatalogValidationError,
    Money,
    PharmacyCatalog,
    load_default_catalog,
)


class CatalogIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_default_catalog()
        cls.branches = cls.catalog.list_branches()
        cls.medications = cls.catalog.list_medications()

    def test_catalog_contains_exactly_three_expected_branches(self) -> None:
        self.assertEqual(len(self.branches), 3)
        self.assertEqual(
            {branch.branch_id for branch in self.branches},
            EXPECTED_BRANCH_IDS,
        )

    def test_branch_ids_are_unique(self) -> None:
        branch_ids = [branch.branch_id for branch in self.branches]

        self.assertEqual(len(branch_ids), len(set(branch_ids)))

    def test_branch_names_are_unique(self) -> None:
        branch_names = [branch.name.casefold() for branch in self.branches]

        self.assertEqual(len(branch_names), len(set(branch_names)))

    def test_branch_names_are_zona_5_zona_15_and_mixco(self) -> None:
        self.assertEqual(
            [branch.name for branch in self.branches],
            ["Zona 5", "Zona 15", "Mixco"],
        )

    def test_catalog_contains_ten_medications(self) -> None:
        self.assertEqual(len(self.medications), 10)

    def test_medication_skus_are_unique(self) -> None:
        skus = [medication.sku for medication in self.medications]

        self.assertEqual(len(skus), len(set(skus)))

    def test_medication_names_are_present_and_unique(self) -> None:
        names = [medication.name for medication in self.medications]

        self.assertTrue(all(name.strip() for name in names))
        self.assertEqual(
            len(names),
            len({name.casefold() for name in names}),
        )

    def test_medications_have_ingredients_and_categories(self) -> None:
        for medication in self.medications:
            with self.subTest(sku=medication.sku):
                self.assertTrue(medication.active_ingredient.strip())
                self.assertTrue(medication.therapeutic_category.strip())

    def test_catalog_covers_required_therapeutic_categories(self) -> None:
        categories = {
            medication.therapeutic_category for medication in self.medications
        }

        self.assertTrue(
            {
                "analgesic_antipyretic",
                "antihistamine",
                "gastrointestinal",
                "cold_and_cough",
                "antibiotic",
            }.issubset(categories)
        )

    def test_medications_have_catalog_dosage_and_contraindications(self) -> None:
        for medication in self.medications:
            with self.subTest(sku=medication.sku):
                self.assertTrue(medication.dosage_information.strip())
                self.assertTrue(medication.contraindications)
                self.assertTrue(
                    all(value.strip() for value in medication.contraindications)
                )

    def test_medication_prices_are_positive_gtq_values(self) -> None:
        for medication in self.medications:
            with self.subTest(sku=medication.sku):
                self.assertGreater(medication.price.amount_centavos, 0)
                self.assertEqual(medication.price.currency, CATALOG_CURRENCY)

    def test_prescription_flags_are_boolean(self) -> None:
        for medication in self.medications:
            with self.subTest(sku=medication.sku):
                self.assertIsInstance(medication.requires_prescription, bool)

    def test_catalog_includes_prescription_and_non_prescription_items(self) -> None:
        flags = {medication.requires_prescription for medication in self.medications}

        self.assertEqual(flags, {False, True})

    def test_aliases_are_non_empty_unique_strings(self) -> None:
        for medication in self.medications:
            with self.subTest(sku=medication.sku):
                normalized_aliases = [
                    alias.casefold() for alias in medication.aliases
                ]
                self.assertTrue(
                    all(
                        isinstance(alias, str) and alias.strip()
                        for alias in medication.aliases
                    )
                )
                self.assertEqual(
                    len(normalized_aliases),
                    len(set(normalized_aliases)),
                )
                self.assertNotIn(medication.name.casefold(), normalized_aliases)

    def test_catalog_lookup_methods_return_expected_values(self) -> None:
        branch = self.catalog.get_branch("zona-15")
        medication = self.catalog.get_medication("MED-ANA-001")

        self.assertEqual(branch.name, "Zona 15")
        self.assertEqual(medication.name, "Acetaminofén 500 mg")
        self.assertIsNone(self.catalog.get_branch("zona-10"))
        self.assertIsNone(self.catalog.get_medication("MED-MISSING"))

    def test_catalog_collections_are_read_only_tuples(self) -> None:
        self.assertIsInstance(self.branches, tuple)
        self.assertIsInstance(self.medications, tuple)

    def test_search_medications_matches_public_catalog_fields(self) -> None:
        cases = {
            "acetaminofen": "MED-ANA-001",
            "paracetamol": "MED-ANA-001",
            "cloruro de sodio": "MED-RES-002",
            "cold_and_cough": "MED-RES-001",
            "med rx 002": "MED-RX-002",
        }

        for query, expected_sku in cases.items():
            with self.subTest(query=query):
                matches = self.catalog.search_medications(query)
                self.assertIn(expected_sku, {item.sku for item in matches})

    def test_search_medications_is_case_and_accent_insensitive(self) -> None:
        matches = self.catalog.search_medications("  SOLUCIÓN SALINA  ")

        self.assertEqual([item.sku for item in matches], ["MED-RES-002"])

    def test_search_medications_can_filter_prescription_items(self) -> None:
        all_matches = self.catalog.search_medications("500 mg")
        otc_matches = self.catalog.search_medications("500 mg", otc_only=True)

        self.assertEqual(
            [item.sku for item in all_matches],
            ["MED-ANA-001", "MED-RX-001", "MED-RX-002"],
        )
        self.assertEqual([item.sku for item in otc_matches], ["MED-ANA-001"])

    def test_search_medications_returns_read_only_empty_tuple(self) -> None:
        matches = self.catalog.search_medications("missing medicine")

        self.assertEqual(matches, ())
        self.assertIsInstance(matches, tuple)

    def test_search_medications_rejects_invalid_query(self) -> None:
        for query in (None, "", "   ", "---", "___", 123):
            with self.subTest(query=query):
                with self.assertRaisesRegex(CatalogQueryError, "query"):
                    self.catalog.search_medications(query)

    def test_search_medications_rejects_non_boolean_filter(self) -> None:
        for otc_only in (None, 1, "true"):
            with self.subTest(otc_only=otc_only):
                with self.assertRaisesRegex(CatalogQueryError, "otc_only"):
                    self.catalog.search_medications(
                        "acetaminofen",
                        otc_only=otc_only,
                    )

    def test_money_rejects_float_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "integer"):
            Money(amount_centavos=12.5)

    def test_money_serializes_without_float(self) -> None:
        price = Money(amount_centavos=1895)

        self.assertEqual(price.to_dict(), {"amount": "18.95", "currency": "GTQ"})

    def test_catalog_rejects_duplicate_medication_sku(self) -> None:
        duplicate = replace(self.medications[1], sku=self.medications[0].sku)

        with self.assertRaisesRegex(
            CatalogValidationError, "Duplicate medication SKU"
        ):
            PharmacyCatalog(
                branches=self.branches,
                medications=(self.medications[0], duplicate),
            )

    def test_catalog_rejects_unexpected_branch(self) -> None:
        invalid_branches = (
            Branch(branch_id="zona-5", name="Zona 5"),
            Branch(branch_id="zona-15", name="Zona 15"),
            Branch(branch_id="zona-16", name="Zona 16"),
        )

        with self.assertRaisesRegex(CatalogValidationError, "exactly"):
            PharmacyCatalog(
                branches=invalid_branches,
                medications=self.medications,
            )


if __name__ == "__main__":
    unittest.main()
