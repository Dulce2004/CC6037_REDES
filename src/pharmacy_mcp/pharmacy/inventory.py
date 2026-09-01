"""Inventario inicial, validado y de solo lectura por sucursal."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from types import MappingProxyType

from .catalog import PharmacyCatalog, load_default_catalog
from .models import InventoryRecord

DEFAULT_INVENTORY_PATH = Path(__file__).with_name("data") / "inventory.json"


class InventoryValidationError(ValueError):
    """Indica que los datos iniciales de inventario son inconsistentes."""


class InventoryLookupError(LookupError):
    """Indica que una consulta usa una sucursal, SKU o combinación inexistente."""


class InventoryRepository:
    """Consulta existencias sin exponer operaciones de modificación."""

    def __init__(
        self,
        *,
        catalog: PharmacyCatalog,
        records: Iterable[InventoryRecord],
    ) -> None:
        if not isinstance(catalog, PharmacyCatalog):
            raise InventoryValidationError(
                "'catalog' must be a PharmacyCatalog instance."
            )

        indexed_records: dict[tuple[str, str], InventoryRecord] = {}
        for record in records:
            if not isinstance(record, InventoryRecord):
                raise InventoryValidationError(
                    "Every inventory item must be an InventoryRecord."
                )
            if catalog.get_branch(record.branch_id) is None:
                raise InventoryValidationError(
                    f"Unknown inventory branch: '{record.branch_id}'."
                )
            if catalog.get_medication(record.sku) is None:
                raise InventoryValidationError(
                    f"Unknown inventory SKU: '{record.sku}'."
                )

            key = (record.branch_id, record.sku)
            if key in indexed_records:
                raise InventoryValidationError(
                    "Duplicate inventory record for "
                    f"branch '{record.branch_id}' and SKU '{record.sku}'."
                )
            indexed_records[key] = record

        expected_keys = {
            (branch.branch_id, medication.sku)
            for branch in catalog.list_branches()
            for medication in catalog.list_medications()
        }
        actual_keys = set(indexed_records)
        missing_keys = expected_keys - actual_keys
        if missing_keys:
            branch_id, sku = sorted(missing_keys)[0]
            raise InventoryValidationError(
                "Missing inventory record for "
                f"branch '{branch_id}' and SKU '{sku}'."
            )

        self._catalog = catalog
        self._records = MappingProxyType(indexed_records)

    @classmethod
    def from_file(
        cls,
        *,
        catalog: PharmacyCatalog,
        inventory_path: str | Path,
    ) -> InventoryRepository:
        """Carga y valida los registros contenidos en ``inventory.json``."""

        raw_records = _read_inventory_array(Path(inventory_path))
        try:
            records = tuple(_parse_inventory_record(item) for item in raw_records)
            return cls(catalog=catalog, records=records)
        except InventoryValidationError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise InventoryValidationError(f"Invalid inventory data: {exc}") from exc

    def get_stock(self, branch_id: str, sku: str) -> int:
        """Devuelve la cantidad disponible para una combinación exacta."""

        self._validate_branch(branch_id)
        self._validate_sku(sku)
        record = self._records.get((branch_id, sku))
        if record is None:
            raise InventoryLookupError(
                f"No stock record for branch '{branch_id}' and SKU '{sku}'."
            )
        return record.quantity

    def list_stock(self, branch_id: str) -> tuple[InventoryRecord, ...]:
        """Lista todos los medicamentos en el orden estable del catálogo."""

        self._validate_branch(branch_id)
        return tuple(
            self._records[(branch_id, medication.sku)]
            for medication in self._catalog.list_medications()
        )

    def get_stock_across_branches(
        self, sku: str
    ) -> tuple[InventoryRecord, ...]:
        """Devuelve el stock de un SKU en las tres sucursales."""

        self._validate_sku(sku)
        return tuple(
            self._records[(branch.branch_id, sku)]
            for branch in self._catalog.list_branches()
        )

    def list_records(self) -> tuple[InventoryRecord, ...]:
        return tuple(self._records.values())

    def _validate_branch(self, branch_id: str) -> None:
        if self._catalog.get_branch(branch_id) is None:
            raise InventoryLookupError(f"Unknown branch: '{branch_id}'.")

    def _validate_sku(self, sku: str) -> None:
        if self._catalog.get_medication(sku) is None:
            raise InventoryLookupError(f"Unknown medication SKU: '{sku}'.")


def load_default_inventory(
    catalog: PharmacyCatalog | None = None,
) -> InventoryRepository:
    """Carga las existencias simuladas incluidas con la aplicación."""

    selected_catalog = catalog if catalog is not None else load_default_catalog()
    return InventoryRepository.from_file(
        catalog=selected_catalog,
        inventory_path=DEFAULT_INVENTORY_PATH,
    )


def _read_inventory_array(path: Path) -> list[object]:
    try:
        raw_data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise InventoryValidationError(
            f"Cannot read inventory file '{path.name}'."
        ) from exc
    except json.JSONDecodeError as exc:
        raise InventoryValidationError(
            f"Inventory file '{path.name}' does not contain valid JSON."
        ) from exc

    if not isinstance(raw_data, list):
        raise InventoryValidationError(
            f"Inventory file '{path.name}' must contain a JSON array."
        )
    if not raw_data:
        raise InventoryValidationError(
            f"Inventory file '{path.name}' must not be empty."
        )
    return raw_data


def _parse_inventory_record(value: object) -> InventoryRecord:
    if not isinstance(value, dict) or set(value) != {
        "branch_id",
        "sku",
        "quantity",
    }:
        raise InventoryValidationError(
            "Every inventory item must contain only branch_id, sku, and quantity."
        )

    return InventoryRecord(
        branch_id=value["branch_id"],
        sku=value["sku"],
        quantity=value["quantity"],
    )
