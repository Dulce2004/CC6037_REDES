"""Pruebas de integridad y consulta del inventario por sucursal."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE_DIRECTORY = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.pharmacy import (  # noqa: E402
    InventoryLookupError,
    InventoryRecord,
    InventoryRepository,
    InventoryValidationError,
    load_default_catalog,
    load_default_inventory,
)


class InventoryRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_default_catalog()
        cls.inventory = load_default_inventory(cls.catalog)
        cls.branches = cls.catalog.list_branches()
        cls.medications = cls.catalog.list_medications()
        cls.records = cls.inventory.list_records()

    def test_catalog_has_three_branches_and_ten_medications(self) -> None:
        self.assertEqual(len(self.branches), 3)
        self.assertEqual(len(self.medications), 10)

    def test_inventory_has_exactly_thirty_records(self) -> None:
        self.assertEqual(len(self.records), 30)

    def test_branch_and_sku_combinations_are_unique(self) -> None:
        keys = [(record.branch_id, record.sku) for record in self.records]

        self.assertEqual(len(keys), len(set(keys)))

    def test_every_record_references_the_catalog(self) -> None:
        for record in self.records:
            with self.subTest(branch_id=record.branch_id, sku=record.sku):
                self.assertIsNotNone(self.catalog.get_branch(record.branch_id))
                self.assertIsNotNone(self.catalog.get_medication(record.sku))

    def test_every_expected_branch_and_sku_combination_exists(self) -> None:
        expected_keys = {
            (branch.branch_id, medication.sku)
            for branch in self.branches
            for medication in self.medications
        }
        actual_keys = {(record.branch_id, record.sku) for record in self.records}

        self.assertEqual(actual_keys, expected_keys)

    def test_quantities_are_non_boolean_nonnegative_integers(self) -> None:
        for record in self.records:
            with self.subTest(branch_id=record.branch_id, sku=record.sku):
                self.assertIsInstance(record.quantity, int)
                self.assertNotIsInstance(record.quantity, bool)
                self.assertGreaterEqual(record.quantity, 0)

    def test_inventory_contains_out_of_stock_and_available_items(self) -> None:
        quantities = {record.quantity for record in self.records}

        self.assertIn(0, quantities)
        self.assertTrue(any(quantity > 0 for quantity in quantities))

    def test_get_stock_returns_expected_quantity(self) -> None:
        self.assertEqual(self.inventory.get_stock("zona-5", "MED-ANA-001"), 25)

    def test_same_sku_has_independent_branch_quantities(self) -> None:
        zona_5 = self.inventory.get_stock("zona-5", "MED-ANA-001")
        zona_15 = self.inventory.get_stock("zona-15", "MED-ANA-001")
        mixco = self.inventory.get_stock("mixco", "MED-ANA-001")

        self.assertEqual((zona_5, zona_15, mixco), (25, 12, 0))

    def test_list_stock_returns_all_medications_for_branch(self) -> None:
        records = self.inventory.list_stock("zona-15")

        self.assertEqual(len(records), 10)
        self.assertTrue(all(record.branch_id == "zona-15" for record in records))
        self.assertEqual(
            {record.sku for record in records},
            {medication.sku for medication in self.medications},
        )

    def test_get_stock_across_branches_returns_three_records(self) -> None:
        records = self.inventory.get_stock_across_branches("MED-ANT-002")

        self.assertEqual(len(records), 3)
        self.assertEqual(
            {record.branch_id: record.quantity for record in records},
            {"zona-5": 0, "zona-15": 16, "mixco": 3},
        )

    def test_unknown_branch_is_rejected(self) -> None:
        with self.assertRaisesRegex(InventoryLookupError, "Unknown branch"):
            self.inventory.get_stock("zona-10", "MED-ANA-001")

        with self.assertRaisesRegex(InventoryLookupError, "Unknown branch"):
            self.inventory.list_stock("zona-10")

    def test_unknown_sku_is_rejected(self) -> None:
        with self.assertRaisesRegex(InventoryLookupError, "Unknown medication SKU"):
            self.inventory.get_stock("zona-5", "MED-MISSING")

        with self.assertRaisesRegex(InventoryLookupError, "Unknown medication SKU"):
            self.inventory.get_stock_across_branches("MED-MISSING")

    def test_inventory_record_rejects_negative_quantity(self) -> None:
        with self.assertRaisesRegex(ValueError, "greater than or equal to zero"):
            InventoryRecord(
                branch_id="zona-5",
                sku="MED-ANA-001",
                quantity=-1,
            )

    def test_inventory_record_rejects_boolean_quantity(self) -> None:
        with self.assertRaisesRegex(ValueError, "integer"):
            InventoryRecord(
                branch_id="zona-5",
                sku="MED-ANA-001",
                quantity=True,
            )

    def test_repository_rejects_duplicate_combination(self) -> None:
        duplicate = self.records[0]

        with self.assertRaisesRegex(InventoryValidationError, "Duplicate"):
            InventoryRepository(
                catalog=self.catalog,
                records=(*self.records, duplicate),
            )

    def test_repository_rejects_unknown_catalog_references(self) -> None:
        unknown_branch = InventoryRecord(
            branch_id="zona-10",
            sku="MED-ANA-001",
            quantity=1,
        )
        unknown_sku = InventoryRecord(
            branch_id="zona-5",
            sku="MED-UNKNOWN",
            quantity=1,
        )

        with self.assertRaisesRegex(InventoryValidationError, "Unknown inventory branch"):
            InventoryRepository(catalog=self.catalog, records=(unknown_branch,))
        with self.assertRaisesRegex(InventoryValidationError, "Unknown inventory SKU"):
            InventoryRepository(catalog=self.catalog, records=(unknown_sku,))

    def test_repository_rejects_missing_combination(self) -> None:
        with self.assertRaisesRegex(InventoryValidationError, "Missing inventory"):
            InventoryRepository(
                catalog=self.catalog,
                records=self.records[:-1],
            )

    def test_loader_rejects_boolean_quantity_in_json_data(self) -> None:
        invalid_json = (
            '[{"branch_id":"zona-5","sku":"MED-ANA-001","quantity":true}]'
        )
        with patch.object(Path, "read_text", return_value=invalid_json):
            with self.assertRaisesRegex(InventoryValidationError, "integer"):
                InventoryRepository.from_file(
                    catalog=self.catalog,
                    inventory_path="inventory.json",
                )


if __name__ == "__main__":
    unittest.main()
