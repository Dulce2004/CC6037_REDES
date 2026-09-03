"""Pruebas del almacén SQLite transaccional de inventario y órdenes."""

from __future__ import annotations

import sqlite3
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier
from uuid import uuid4

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = PROJECT_DIRECTORY / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.pharmacy import (  # noqa: E402
    InsufficientStockError,
    OrderExecutionError,
    OrderItemRequest,
    OrderLookupError,
    PrescriptionRequiredError,
    SQLitePharmacyStore,
    StoreInitializationError,
    load_default_catalog,
    load_default_inventory,
)


class SQLitePharmacyStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_default_catalog()
        self.initial_inventory = load_default_inventory(self.catalog)
        runtime_directory = PROJECT_DIRECTORY / "runtime"
        runtime_directory.mkdir(exist_ok=True)
        self.database_path = runtime_directory / (
            f"test-orders-{uuid4().hex}.sqlite3"
        )
        self.addCleanup(self._remove_database_files)
        self.store = SQLitePharmacyStore(
            database_path=self.database_path,
            catalog=self.catalog,
        )
        self.store.initialize(self.initial_inventory)
        self.addCleanup(self.store.close)

    def _remove_database_files(self) -> None:
        for suffix in ("", "-shm", "-wal"):
            Path(f"{self.database_path}{suffix}").unlink(missing_ok=True)

    def test_constructor_does_not_create_database_before_initialize(self) -> None:
        uninitialized_path = self.database_path.with_name(
            f"uninitialized-{uuid4().hex}.sqlite3"
        )
        self.addCleanup(uninitialized_path.unlink, missing_ok=True)
        store = SQLitePharmacyStore(
            database_path=uninitialized_path,
            catalog=self.catalog,
        )

        self.assertFalse(uninitialized_path.exists())
        with self.assertRaisesRegex(StoreInitializationError, "initialized"):
            store.get_stock("zona-5", "MED-ANA-001")

    def test_initialization_seeds_validated_json_inventory(self) -> None:
        self.assertEqual(self.store.get_stock("zona-5", "MED-ANA-001"), 25)
        self.assertEqual(len(self.store.list_records()), 30)

    def test_successful_order_decrements_stock_and_is_retrievable(self) -> None:
        order = self.store.create_order(
            branch_id="zona-5",
            items=(
                OrderItemRequest(sku="MED-ANA-001", quantity=2),
                OrderItemRequest(sku="MED-ANA-002", quantity=1),
            ),
        )

        self.assertEqual(order.status, "created")
        self.assertEqual(order.total_centavos, 6265)
        self.assertEqual(self.store.get_stock("zona-5", "MED-ANA-001"), 23)
        self.assertEqual(self.store.get_stock("zona-5", "MED-ANA-002"), 7)
        self.assertEqual(self.store.get_order(order.order_id), order)

    def test_failed_multi_item_order_rolls_back_every_quantity(self) -> None:
        with self.assertRaises(InsufficientStockError):
            self.store.create_order(
                branch_id="zona-5",
                items=(
                    OrderItemRequest(sku="MED-ANA-001", quantity=2),
                    OrderItemRequest(sku="MED-ANT-002", quantity=1),
                ),
            )

        self.assertEqual(self.store.get_stock("zona-5", "MED-ANA-001"), 25)
        self.assertEqual(self.store.get_stock("zona-5", "MED-ANT-002"), 0)
        with closing(sqlite3.connect(self.database_path)) as connection:
            order_count = connection.execute(
                "SELECT COUNT(*) FROM orders"
            ).fetchone()[0]
        self.assertEqual(order_count, 0)

    def test_database_constraint_rejects_negative_stock(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    UPDATE inventory
                    SET quantity = -1
                    WHERE branch_id = 'zona-5' AND sku = 'MED-ANA-001'
                    """
                )

        self.assertEqual(self.store.get_stock("zona-5", "MED-ANA-001"), 25)

    def test_prescription_medication_requires_simulated_identifier(self) -> None:
        with self.assertRaises(PrescriptionRequiredError):
            self.store.create_order(
                branch_id="zona-5",
                items=(OrderItemRequest(sku="MED-RX-001", quantity=1),),
            )

        self.assertEqual(self.store.get_stock("zona-5", "MED-RX-001"), 6)

    def test_format_valid_prescription_reference_allows_simulated_order(self) -> None:
        order = self.store.create_order(
            branch_id="zona-5",
            items=(OrderItemRequest(sku="MED-RX-001", quantity=1),),
            prescription_id="RX-ACADEMIC-001",
        )

        self.assertTrue(order.prescription_required)
        self.assertTrue(order.prescription_provided)
        self.assertEqual(self.store.get_stock("zona-5", "MED-RX-001"), 5)

    def test_unknown_branch_or_sku_does_not_mutate_stock(self) -> None:
        with self.assertRaisesRegex(OrderExecutionError, "Unknown branch"):
            self.store.create_order(
                branch_id="zona-10",
                items=(OrderItemRequest(sku="MED-ANA-001", quantity=1),),
            )
        with self.assertRaisesRegex(OrderExecutionError, "Unknown medication"):
            self.store.create_order(
                branch_id="zona-5",
                items=(OrderItemRequest(sku="MED-MISSING", quantity=1),),
            )

        self.assertEqual(self.store.get_stock("zona-5", "MED-ANA-001"), 25)

    def test_order_quantity_rejects_zero_negative_boolean_and_non_integer(self) -> None:
        for quantity in (0, -1, True, 1.5):
            with self.subTest(quantity=quantity):
                with self.assertRaisesRegex(ValueError, "quantity"):
                    OrderItemRequest(
                        sku="MED-ANA-001",
                        quantity=quantity,  # type: ignore[arg-type]
                    )

    def test_unknown_order_is_rejected(self) -> None:
        with self.assertRaisesRegex(OrderLookupError, "Unknown order ID"):
            self.store.get_order("ORD-MISSING")

    def test_reinitialization_preserves_orders_and_updated_stock(self) -> None:
        order = self.store.create_order(
            branch_id="zona-5",
            items=(OrderItemRequest(sku="MED-ANA-001", quantity=3),),
        )
        second_store = SQLitePharmacyStore(
            database_path=self.database_path,
            catalog=self.catalog,
        )
        second_store.initialize(self.initial_inventory)
        self.addCleanup(second_store.close)

        self.assertEqual(second_store.get_stock("zona-5", "MED-ANA-001"), 22)
        self.assertEqual(second_store.get_order(order.order_id), order)

    def test_concurrent_orders_cannot_oversell_units(self) -> None:
        second_store = SQLitePharmacyStore(
            database_path=self.database_path,
            catalog=self.catalog,
        )
        second_store.initialize(self.initial_inventory)
        self.addCleanup(second_store.close)
        barrier = Barrier(2)

        def attempt_order(store: SQLitePharmacyStore) -> str:
            barrier.wait()
            try:
                store.create_order(
                    branch_id="mixco",
                    items=(OrderItemRequest(sku="MED-ANT-002", quantity=2),),
                )
            except InsufficientStockError:
                return "insufficient"
            return "created"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(attempt_order, (self.store, second_store))
            )

        self.assertCountEqual(results, ["created", "insufficient"])
        self.assertEqual(self.store.get_stock("mixco", "MED-ANT-002"), 1)


if __name__ == "__main__":
    unittest.main()
