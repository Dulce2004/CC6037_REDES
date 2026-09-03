"""Adaptadores MCP de consultas de catálogo, interacciones e inventario."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pharmacy_mcp.jsonrpc import InvalidParamsError
from pharmacy_mcp.jsonrpc.messages import JsonValue
from pharmacy_mcp.pharmacy import (
    EXPECTED_BRANCH_IDS,
    INTERACTION_DISCLAIMER,
    CatalogQueryError,
    InteractionLookupError,
    InteractionQueryError,
    InteractionRepository,
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

CHECK_INTERACTIONS_NAME = "check_interactions"
CHECK_INTERACTIONS_DESCRIPTION = (
    "Checks a requested medication against current medications and allergies "
    "using controlled, non-exhaustive simulated rules."
)
CHECK_INTERACTIONS_INPUT_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "medication_sku": {
            "type": "string",
            "minLength": 1,
            "pattern": "^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$",
        },
        "current_medications": {
            "type": "array",
            "items": {
                "type": "string",
                "minLength": 1,
                "pattern": "^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$",
            },
            "maxItems": 20,
            "uniqueItems": True,
            "default": [],
        },
        "allergies": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 200},
            "maxItems": 20,
            "uniqueItems": True,
            "default": [],
        },
    },
    "required": ["medication_sku"],
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
    interactions: InteractionRepository

    def __post_init__(self) -> None:
        if not isinstance(self.catalog, PharmacyCatalog):
            raise TypeError("'catalog' must be a PharmacyCatalog instance.")
        if not isinstance(self.inventory, InventoryRepository):
            raise TypeError("'inventory' must be an InventoryRepository instance.")
        if not isinstance(self.interactions, InteractionRepository):
            raise TypeError(
                "'interactions' must be an InteractionRepository instance."
            )

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

    def check_interactions(self, arguments: ToolArguments) -> JsonValue:
        _reject_unexpected_arguments(
            arguments,
            {"medication_sku", "current_medications", "allergies"},
        )
        medication_sku = _required_identifier(
            arguments,
            "medication_sku",
        ).upper()
        current_medications = _optional_identifier_array(
            arguments,
            "current_medications",
        )
        allergies = _optional_string_array(arguments, "allergies")

        try:
            alerts = self.interactions.check_interactions(
                medication_sku,
                current_medications,
                allergies,
            )
        except InteractionQueryError as exc:
            raise InvalidParamsError(str(exc)) from exc
        except InteractionLookupError as exc:
            return _tool_error(f"{exc} {INTERACTION_DISCLAIMER}")

        medication = self.catalog.get_medication(medication_sku)
        if medication is None:
            return _tool_error(
                f"Unknown medication SKU: '{medication_sku}'. "
                f"{INTERACTION_DISCLAIMER}"
            )

        serialized_alerts: list[dict[str, JsonValue]] = []
        for alert in alerts:
            serialized = alert.to_dict()
            related_sku = serialized.get("related_sku")
            if isinstance(related_sku, str):
                related_medication = self.catalog.get_medication(related_sku)
                if related_medication is not None:
                    serialized["related_medication_name"] = related_medication.name
            serialized_alerts.append(serialized)

        highest_severity = _highest_interaction_severity(serialized_alerts)
        if serialized_alerts:
            summary = (
                f"Found {len(serialized_alerts)} simulated alert(s); highest "
                f"severity: {highest_severity}. Obtain professional review "
                "before using the requested medication."
            )
        else:
            summary = (
                "No alerts were found in the controlled simulated dataset. "
                "This does not establish that the medication is safe."
            )
        prescription_notice = (
            " This medication requires a prescription; this tool does not "
            "recommend or authorize its use."
            if medication.requires_prescription
            else ""
        )
        text = (
            f"Interaction check for {medication.sku} - {medication.name}. "
            f"{summary}{prescription_notice} {INTERACTION_DISCLAIMER}"
        )
        return _tool_result(
            text,
            {
                "medication": {
                    "sku": medication.sku,
                    "name": medication.name,
                    "requires_prescription": medication.requires_prescription,
                },
                "current_medications": current_medications,
                "allergies": allergies,
                "alert_count": len(serialized_alerts),
                "highest_severity": highest_severity,
                "alerts": serialized_alerts,
                "exhaustive": False,
                "safety_established": False,
                "disclaimer": INTERACTION_DISCLAIMER,
            },
        )

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


def _optional_identifier_array(
    arguments: ToolArguments,
    name: str,
) -> list[str]:
    value = arguments.get(name, [])
    if not isinstance(value, list):
        raise InvalidParamsError(f"'{name}' must be an array.")
    if len(value) > 20:
        raise InvalidParamsError(f"'{name}' must contain at most 20 items.")
    normalized = [
        _validate_identifier_value(item, name).upper() for item in value
    ]
    if len(set(normalized)) != len(normalized):
        raise InvalidParamsError(f"'{name}' must not contain duplicate values.")
    return normalized


def _optional_string_array(
    arguments: ToolArguments,
    name: str,
) -> list[str]:
    value = arguments.get(name, [])
    if not isinstance(value, list):
        raise InvalidParamsError(f"'{name}' must be an array.")
    if len(value) > 20:
        raise InvalidParamsError(f"'{name}' must contain at most 20 items.")

    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise InvalidParamsError(
                f"Every item in '{name}' must be a non-empty string."
            )
        text = item.strip()
        if len(text) > 200:
            raise InvalidParamsError(
                f"Every item in '{name}' must contain at most 200 characters."
            )
        key = text.casefold()
        if key in seen:
            raise InvalidParamsError(
                f"'{name}' must not contain duplicate values."
            )
        normalized.append(text)
        seen.add(key)
    return normalized


def _validate_identifier_value(value: JsonValue, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidParamsError(
            f"Every item in '{name}' must be a non-empty string."
        )
    identifier = value.strip()
    if _IDENTIFIER_PATTERN.fullmatch(identifier) is None:
        raise InvalidParamsError(
            f"Every item in '{name}' must use letters, digits, and single "
            "hyphen separators."
        )
    return identifier


def _highest_interaction_severity(
    alerts: list[dict[str, JsonValue]],
) -> str:
    severities = {alert.get("severity") for alert in alerts}
    if "high" in severities:
        return "high"
    if "moderate" in severities:
        return "moderate"
    return "none"


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
