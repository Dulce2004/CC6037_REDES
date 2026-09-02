"""Adaptadores MCP de solo lectura para catálogo e inventario de farmacia."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pharmacy_mcp.jsonrpc import InvalidParamsError
from pharmacy_mcp.jsonrpc.messages import JsonValue
from pharmacy_mcp.pharmacy import (
    EXPECTED_BRANCH_IDS,
    CatalogQueryError,
    InventoryLookupError,
    InventoryRepository,
    Medication,
    PharmacyCatalog,
)

from .handlers import ToolArguments

SEARCH_MEDICATIONS_NAME = "search_medications"
SEARCH_MEDICATIONS_DESCRIPTION = (
    "Searches the simulated medication catalog by text and optional OTC status."
)
SEARCH_MEDICATIONS_INPUT_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "minLength": 1},
        "otc_only": {"type": "boolean", "default": False},
    },
    "required": ["query"],
    "additionalProperties": False,
}

GET_MEDICATION_DETAILS_NAME = "get_medication_details"
GET_MEDICATION_DETAILS_DESCRIPTION = (
    "Returns complete simulated catalog details for one medication SKU."
)
GET_MEDICATION_DETAILS_INPUT_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "sku": {
            "type": "string",
            "minLength": 1,
            "pattern": "^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$",
        }
    },
    "required": ["sku"],
    "additionalProperties": False,
}

CHECK_STOCK_NAME = "check_stock"
CHECK_STOCK_DESCRIPTION = (
    "Checks read-only inventory for one medication SKU at one or all branches."
)
CHECK_STOCK_INPUT_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "sku": {
            "type": "string",
            "minLength": 1,
            "pattern": "^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$",
        },
        "branch_id": {
            "type": "string",
            "enum": sorted(EXPECTED_BRANCH_IDS),
        },
    },
    "required": ["sku"],
    "additionalProperties": False,
}

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")


@dataclass(frozen=True, slots=True, kw_only=True)
class PharmacyQueryHandlers:
    """Handlers enlazados a repositorios de dominio validados y de solo lectura."""

    catalog: PharmacyCatalog
    inventory: InventoryRepository

    def __post_init__(self) -> None:
        if not isinstance(self.catalog, PharmacyCatalog):
            raise TypeError("'catalog' must be a PharmacyCatalog instance.")
        if not isinstance(self.inventory, InventoryRepository):
            raise TypeError("'inventory' must be an InventoryRepository instance.")

    def search_medications(self, arguments: ToolArguments) -> JsonValue:
        _reject_unexpected_arguments(arguments, {"query", "otc_only"})
        query = _required_string(arguments, "query")
        otc_only = arguments.get("otc_only", False)

        try:
            medications = self.catalog.search_medications(
                query,
                otc_only=otc_only,
            )
        except CatalogQueryError as exc:
            raise InvalidParamsError(str(exc)) from exc

        summaries = [_medication_summary(medication) for medication in medications]
        if medications:
            names = "; ".join(
                f"{medication.sku} - {medication.name}"
                for medication in medications
            )
            text = f"Found {len(medications)} medication(s): {names}."
        else:
            text = f"No medications found for query '{query}'."

        return _tool_result(
            f"{text} Simulated catalog data; not medical advice.",
            {
                "query": query,
                "otc_only": otc_only,
                "count": len(summaries),
                "medications": summaries,
            },
        )

    def get_medication_details(self, arguments: ToolArguments) -> JsonValue:
        _reject_unexpected_arguments(arguments, {"sku"})
        sku = _required_identifier(arguments, "sku").upper()
        medication = self.catalog.get_medication(sku)
        if medication is None:
            return _tool_error(f"Unknown medication SKU: '{sku}'.")

        details = _medication_details(medication)
        text = (
            f"{medication.sku} - {medication.name}; active ingredient: "
            f"{medication.active_ingredient}; category: "
            f"{medication.therapeutic_category}; price: "
            f"{medication.price.currency} {medication.price.amount}; "
            f"prescription required: "
            f"{'yes' if medication.requires_prescription else 'no'}. "
            "Simulated catalog data; not medical advice."
        )
        return _tool_result(text, {"medication": details})

    def check_stock(self, arguments: ToolArguments) -> JsonValue:
        _reject_unexpected_arguments(arguments, {"sku", "branch_id"})
        sku = _required_identifier(arguments, "sku").upper()

        try:
            if "branch_id" not in arguments:
                records = self.inventory.get_stock_across_branches(sku)
                stock = [
                    self._stock_item(record.branch_id, record.quantity)
                    for record in records
                ]
            else:
                branch_id = _required_identifier(
                    arguments,
                    "branch_id",
                ).casefold()
                quantity = self.inventory.get_stock(branch_id, sku)
                stock = [self._stock_item(branch_id, quantity)]
        except InventoryLookupError as exc:
            return _tool_error(str(exc))

        medication = self.catalog.get_medication(sku)
        if medication is None:
            return _tool_error(f"Unknown medication SKU: '{sku}'.")

        locations = "; ".join(
            f"{item['branch_name']} ({item['branch_id']}): {item['quantity']}"
            for item in stock
        )
        text = (
            f"Stock for {medication.sku} - {medication.name}: {locations}. "
            "Simulated read-only inventory."
        )
        return _tool_result(
            text,
            {
                "sku": medication.sku,
                "medication_name": medication.name,
                "stock": stock,
            },
        )

    def _stock_item(self, branch_id: str, quantity: int) -> dict[str, JsonValue]:
        branch = self.catalog.get_branch(branch_id)
        if branch is None:
            raise RuntimeError("Inventory references an unknown branch.")
        return {
            "branch_id": branch.branch_id,
            "branch_name": branch.name,
            "quantity": quantity,
            "available": quantity > 0,
        }


def _required_string(arguments: ToolArguments, name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise InvalidParamsError(f"'{name}' must be a non-empty string.")
    return value.strip()


def _required_identifier(arguments: ToolArguments, name: str) -> str:
    value = _required_string(arguments, name)
    if _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise InvalidParamsError(
            f"'{name}' must use letters, digits, and single hyphen separators."
        )
    return value


def _reject_unexpected_arguments(
    arguments: ToolArguments,
    allowed: set[str],
) -> None:
    unexpected = sorted(set(arguments) - allowed)
    if unexpected:
        raise InvalidParamsError(
            "Unexpected tool arguments: " + ", ".join(unexpected) + "."
        )


def _medication_summary(medication: Medication) -> dict[str, JsonValue]:
    return {
        "sku": medication.sku,
        "name": medication.name,
        "active_ingredient": medication.active_ingredient,
        "therapeutic_category": medication.therapeutic_category,
        "requires_prescription": medication.requires_prescription,
        "price": medication.price.to_dict(),
    }


def _medication_details(medication: Medication) -> dict[str, JsonValue]:
    return {
        "sku": medication.sku,
        "name": medication.name,
        "aliases": list(medication.aliases),
        "active_ingredient": medication.active_ingredient,
        "therapeutic_category": medication.therapeutic_category,
        "dosage_information": medication.dosage_information,
        "contraindications": list(medication.contraindications),
        "requires_prescription": medication.requires_prescription,
        "price": medication.price.to_dict(),
    }


def _tool_result(text: str, structured_content: JsonValue) -> dict[str, JsonValue]:
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": structured_content,
    }


def _tool_error(text: str) -> dict[str, JsonValue]:
    return {
        "content": [{"type": "text", "text": text}],
        "isError": True,
    }
