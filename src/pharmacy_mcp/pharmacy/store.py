"""Almacén SQLite transaccional para inventario y órdenes simuladas."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .catalog import PharmacyCatalog
from .inventory import InventoryLookupError, InventoryRepository
from .models import InventoryRecord, OrderItemRequest, OrderLine, OrderRecord

SCHEMA_VERSION = "1"
PRESCRIPTION_ID_PATTERN = re.compile(
    r"^RX-[A-Z0-9]+(?:-[A-Z0-9]+)*$"
)


class StoreInitializationError(RuntimeError):
    """Indica que la base SQLite no puede inicializarse de forma segura."""


class OrderValidationError(ValueError):
    """Indica datos de orden inválidos antes de ejecutar una transacción."""


class OrderExecutionError(RuntimeError):
    """Indica una condición de dominio que impide crear o leer una orden."""


class OrderLookupError(OrderExecutionError):
    """Indica que no existe la orden solicitada."""


class InsufficientStockError(OrderExecutionError):
    """Indica que una orden completa no puede reservarse."""


class PrescriptionRequiredError(OrderExecutionError):
    """Indica que falta una referencia de receta simulada requerida."""


class SQLitePharmacyStore:
    """Estado persistente compartido por consultas de stock y órdenes.

    Construir una instancia no crea archivos ni tablas. ``initialize`` debe
    invocarse explícitamente con el inventario JSON ya validado.
    """

    def __init__(self, *, database_path: str | Path, catalog: PharmacyCatalog) -> None:
        if not isinstance(catalog, PharmacyCatalog):
            raise TypeError("'catalog' must be a PharmacyCatalog instance.")
        raw_path = str(database_path)
        if not raw_path.strip():
            raise ValueError("'database_path' must not be empty.")

        self._database_path = (
            None if raw_path == ":memory:" else Path(database_path).resolve()
        )
        self._connection_target = (
            f"file:pharmacy-mcp-{uuid4().hex}?mode=memory&cache=shared"
            if self._database_path is None
            else str(self._database_path)
        )
        self._connection_uri = self._database_path is None
        self._anchor_connection: sqlite3.Connection | None = None
        self._catalog = catalog
        self._initialized = False

    @property
    def database_path(self) -> Path | None:
        return self._database_path

    def initialize(self, initial_inventory: InventoryRepository) -> None:
        """Crea el esquema y siembra una base nueva dentro de una transacción."""

        if not isinstance(initial_inventory, InventoryRepository):
            raise TypeError(
                "'initial_inventory' must be an InventoryRepository instance."
            )

        if self._database_path is not None:
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect(require_initialized=False)
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            self._create_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                version_row = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'schema_version'"
                ).fetchone()
                if version_row is None:
                    self._ensure_unseeded_tables_are_empty(connection)
                    self._seed(connection, initial_inventory)
                elif version_row["value"] != SCHEMA_VERSION:
                    raise StoreInitializationError(
                        "Unsupported pharmacy database schema version."
                    )
                else:
                    self._validate_persisted_catalog(connection)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except (OSError, sqlite3.Error) as exc:
            connection.close()
            raise StoreInitializationError(
                "Cannot initialize the configured pharmacy database."
            ) from exc
        except Exception:
            connection.close()
            raise
        else:
            if self._connection_uri:
                self._anchor_connection = connection
            else:
                connection.close()

        self._initialized = True

    def close(self) -> None:
        """Libera la conexión que mantiene viva una base aislada en memoria."""

        if self._anchor_connection is not None:
            self._anchor_connection.close()
            self._anchor_connection = None
        self._initialized = False

    def get_stock(self, branch_id: str, sku: str) -> int:
        self._validate_branch(branch_id)
        self._validate_sku(sku)
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT quantity
                FROM inventory
                WHERE branch_id = ? AND sku = ?
                """,
                (branch_id, sku),
            ).fetchone()
        if row is None:
            raise InventoryLookupError(
                f"No stock record for branch '{branch_id}' and SKU '{sku}'."
            )
        return int(row["quantity"])

    def list_stock(self, branch_id: str) -> tuple[InventoryRecord, ...]:
        self._validate_branch(branch_id)
        with closing(self._connect()) as connection:
            quantities = {
                row["sku"]: int(row["quantity"])
                for row in connection.execute(
                    "SELECT sku, quantity FROM inventory WHERE branch_id = ?",
                    (branch_id,),
                )
            }
        return tuple(
            InventoryRecord(
                branch_id=branch_id,
                sku=medication.sku,
                quantity=self._required_quantity(
                    quantities,
                    medication.sku,
                    branch_id,
                    medication.sku,
                ),
            )
            for medication in self._catalog.list_medications()
        )

    def get_stock_across_branches(
        self, sku: str
    ) -> tuple[InventoryRecord, ...]:
        self._validate_sku(sku)
        with closing(self._connect()) as connection:
            quantities = {
                row["branch_id"]: int(row["quantity"])
                for row in connection.execute(
                    "SELECT branch_id, quantity FROM inventory WHERE sku = ?",
                    (sku,),
                )
            }
        return tuple(
            InventoryRecord(
                branch_id=branch.branch_id,
                sku=sku,
                quantity=self._required_quantity(
                    quantities,
                    branch.branch_id,
                    branch.branch_id,
                    sku,
                ),
            )
            for branch in self._catalog.list_branches()
        )

    def list_records(self) -> tuple[InventoryRecord, ...]:
        return tuple(
            record
            for branch in self._catalog.list_branches()
            for record in self.list_stock(branch.branch_id)
        )

    def create_order(
        self,
        *,
        branch_id: str,
        items: Iterable[OrderItemRequest],
        prescription_id: str | None = None,
    ) -> OrderRecord:
        """Crea una orden y descuenta todas sus unidades de forma atómica."""

        self._validate_branch_for_order(branch_id)
        item_list = tuple(items)
        self._validate_order_items(item_list)
        normalized_prescription = self._validate_prescription_id(prescription_id)

        medications = []
        for item in item_list:
            medication = self._catalog.get_medication(item.sku)
            if medication is None:
                raise OrderExecutionError(
                    f"Unknown medication SKU: '{item.sku}'."
                )
            medications.append(medication)

        prescription_required = any(
            medication.requires_prescription for medication in medications
        )
        if prescription_required and normalized_prescription is None:
            raise PrescriptionRequiredError(
                "A simulated prescription identifier is required for this order."
            )

        order_id = f"ORD-{uuid4().hex.upper()}"
        created_at = datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for item in item_list:
                    row = connection.execute(
                        """
                        SELECT quantity
                        FROM inventory
                        WHERE branch_id = ? AND sku = ?
                        """,
                        (branch_id, item.sku),
                    ).fetchone()
                    if row is None:
                        raise OrderExecutionError(
                            "No stock record for branch "
                            f"'{branch_id}' and SKU '{item.sku}'."
                        )
                    available = int(row["quantity"])
                    if available < item.quantity:
                        raise InsufficientStockError(
                            f"Insufficient stock for SKU '{item.sku}' at branch "
                            f"'{branch_id}': requested {item.quantity}, "
                            f"available {available}."
                        )

                connection.execute(
                    """
                    INSERT INTO orders (
                        order_id,
                        branch_id,
                        status,
                        prescription_id,
                        prescription_required,
                        created_at
                    ) VALUES (?, ?, 'created', ?, ?, ?)
                    """,
                    (
                        order_id,
                        branch_id,
                        normalized_prescription,
                        int(prescription_required),
                        created_at,
                    ),
                )

                for position, (item, medication) in enumerate(
                    zip(item_list, medications, strict=True)
                ):
                    updated = connection.execute(
                        """
                        UPDATE inventory
                        SET quantity = quantity - ?
                        WHERE branch_id = ?
                          AND sku = ?
                          AND quantity >= ?
                        """,
                        (item.quantity, branch_id, item.sku, item.quantity),
                    )
                    if updated.rowcount != 1:
                        raise InsufficientStockError(
                            f"Stock changed while reserving SKU '{item.sku}'."
                        )
                    connection.execute(
                        """
                        INSERT INTO order_items (
                            order_id,
                            position,
                            sku,
                            quantity,
                            unit_price_centavos
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            order_id,
                            position,
                            item.sku,
                            item.quantity,
                            medication.price.amount_centavos,
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        return self.get_order(order_id)

    def get_order(self, order_id: str) -> OrderRecord:
        with closing(self._connect()) as connection:
            order_row = connection.execute(
                """
                SELECT order_id, branch_id, status, prescription_id,
                       prescription_required, created_at
                FROM orders
                WHERE order_id = ?
                """,
                (order_id,),
            ).fetchone()
            if order_row is None:
                raise OrderLookupError(f"Unknown order ID: '{order_id}'.")
            item_rows = connection.execute(
                """
                SELECT sku, quantity, unit_price_centavos
                FROM order_items
                WHERE order_id = ?
                ORDER BY position
                """,
                (order_id,),
            ).fetchall()

        return OrderRecord(
            order_id=order_row["order_id"],
            branch_id=order_row["branch_id"],
            status=order_row["status"],
            items=tuple(
                OrderLine(
                    sku=row["sku"],
                    quantity=int(row["quantity"]),
                    unit_price_centavos=int(row["unit_price_centavos"]),
                )
                for row in item_rows
            ),
            prescription_required=bool(order_row["prescription_required"]),
            prescription_provided=order_row["prescription_id"] is not None,
            created_at=order_row["created_at"],
        )

    def _connect(self, *, require_initialized: bool = True) -> sqlite3.Connection:
        if require_initialized and not self._initialized:
            raise StoreInitializationError(
                "The pharmacy database must be initialized before use."
            )
        connection = sqlite3.connect(
            self._connection_target,
            timeout=10.0,
            isolation_level=None,
            uri=self._connection_uri,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS branches (
                branch_id TEXT PRIMARY KEY,
                name TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS medications (
                sku TEXT PRIMARY KEY,
                requires_prescription INTEGER NOT NULL
                    CHECK (requires_prescription IN (0, 1)),
                price_centavos INTEGER NOT NULL CHECK (price_centavos > 0)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS inventory (
                branch_id TEXT NOT NULL REFERENCES branches(branch_id),
                sku TEXT NOT NULL REFERENCES medications(sku),
                quantity INTEGER NOT NULL CHECK (quantity >= 0),
                PRIMARY KEY (branch_id, sku)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                branch_id TEXT NOT NULL REFERENCES branches(branch_id),
                status TEXT NOT NULL CHECK (status = 'created'),
                prescription_id TEXT,
                prescription_required INTEGER NOT NULL
                    CHECK (prescription_required IN (0, 1)),
                created_at TEXT NOT NULL,
                CHECK (
                    prescription_required = 0 OR prescription_id IS NOT NULL
                )
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS order_items (
                order_id TEXT NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
                position INTEGER NOT NULL CHECK (position >= 0),
                sku TEXT NOT NULL REFERENCES medications(sku),
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                unit_price_centavos INTEGER NOT NULL
                    CHECK (unit_price_centavos > 0),
                PRIMARY KEY (order_id, position),
                UNIQUE (order_id, sku)
            )
            """,
        )
        for statement in statements:
            connection.execute(statement)

    @staticmethod
    def _ensure_unseeded_tables_are_empty(connection: sqlite3.Connection) -> None:
        for table in ("branches", "medications", "inventory", "orders"):
            count = connection.execute(
                f"SELECT COUNT(*) AS count FROM {table}"
            ).fetchone()["count"]
            if count:
                raise StoreInitializationError(
                    "Pharmacy database contains data without schema metadata."
                )

    def _seed(
        self,
        connection: sqlite3.Connection,
        initial_inventory: InventoryRepository,
    ) -> None:
        connection.executemany(
            "INSERT INTO branches (branch_id, name) VALUES (?, ?)",
            (
                (branch.branch_id, branch.name)
                for branch in self._catalog.list_branches()
            ),
        )
        connection.executemany(
            """
            INSERT INTO medications (
                sku, requires_prescription, price_centavos
            ) VALUES (?, ?, ?)
            """,
            (
                (
                    medication.sku,
                    int(medication.requires_prescription),
                    medication.price.amount_centavos,
                )
                for medication in self._catalog.list_medications()
            ),
        )
        connection.executemany(
            """
            INSERT INTO inventory (branch_id, sku, quantity)
            VALUES (?, ?, ?)
            """,
            (
                (record.branch_id, record.sku, record.quantity)
                for record in initial_inventory.list_records()
            ),
        )
        connection.execute(
            "INSERT INTO metadata (key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )

    def _validate_persisted_catalog(self, connection: sqlite3.Connection) -> None:
        persisted_branches = {
            (row["branch_id"], row["name"])
            for row in connection.execute("SELECT branch_id, name FROM branches")
        }
        expected_branches = {
            (branch.branch_id, branch.name)
            for branch in self._catalog.list_branches()
        }
        persisted_medications = {
            (
                row["sku"],
                bool(row["requires_prescription"]),
                int(row["price_centavos"]),
            )
            for row in connection.execute(
                """
                SELECT sku, requires_prescription, price_centavos
                FROM medications
                """
            )
        }
        expected_medications = {
            (
                medication.sku,
                medication.requires_prescription,
                medication.price.amount_centavos,
            )
            for medication in self._catalog.list_medications()
        }
        expected_inventory_keys = {
            (branch.branch_id, medication.sku)
            for branch in self._catalog.list_branches()
            for medication in self._catalog.list_medications()
        }
        persisted_inventory_keys = {
            (row["branch_id"], row["sku"])
            for row in connection.execute("SELECT branch_id, sku FROM inventory")
        }
        if (
            persisted_branches != expected_branches
            or persisted_medications != expected_medications
            or persisted_inventory_keys != expected_inventory_keys
        ):
            raise StoreInitializationError(
                "Persisted pharmacy data is incompatible with the current catalog."
            )

    def _validate_branch(self, branch_id: str) -> None:
        if self._catalog.get_branch(branch_id) is None:
            raise InventoryLookupError(f"Unknown branch: '{branch_id}'.")

    def _validate_sku(self, sku: str) -> None:
        if self._catalog.get_medication(sku) is None:
            raise InventoryLookupError(f"Unknown medication SKU: '{sku}'.")

    def _validate_branch_for_order(self, branch_id: str) -> None:
        if self._catalog.get_branch(branch_id) is None:
            raise OrderExecutionError(f"Unknown branch: '{branch_id}'.")

    @staticmethod
    def _validate_order_items(items: tuple[OrderItemRequest, ...]) -> None:
        if not items:
            raise OrderValidationError("An order must contain at least one item.")
        if not all(isinstance(item, OrderItemRequest) for item in items):
            raise OrderValidationError(
                "Order items must be OrderItemRequest instances."
            )
        skus = [item.sku for item in items]
        if len(skus) != len(set(skus)):
            raise OrderValidationError("Order items must not repeat a SKU.")

    @staticmethod
    def _validate_prescription_id(prescription_id: str | None) -> str | None:
        if prescription_id is None:
            return None
        if (
            not isinstance(prescription_id, str)
            or len(prescription_id) > 64
            or PRESCRIPTION_ID_PATTERN.fullmatch(prescription_id) is None
        ):
            raise OrderValidationError(
                "'prescription_id' must be a simulated RX identifier."
            )
        return prescription_id

    @staticmethod
    def _required_quantity(
        quantities: dict[str, int],
        key: str,
        branch_id: str,
        sku: str,
    ) -> int:
        try:
            return quantities[key]
        except KeyError as exc:
            raise InventoryLookupError(
                f"No stock record for branch '{branch_id}' and SKU '{sku}'."
            ) from exc
