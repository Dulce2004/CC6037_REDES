"""Adaptadores MCP para crear y consultar órdenes simuladas."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pharmacy_mcp.jsonrpc import InvalidParamsError
from pharmacy_mcp.jsonrpc.messages import JsonValue
from pharmacy_mcp.pharmacy import (
    CATALOG_CURRENCY,
    EXPECTED_BRANCH_IDS,
    OrderExecutionError,
    OrderItemRequest,
    OrderLookupError,
    OrderRecord,
    OrderValidationError,
    PharmacyCatalog,
    SQLitePharmacyStore,
)

from .handlers import ToolArguments

CREATE_ORDER_NAME = "create_order"
CREATE_ORDER_DESCRIPTION = (
    "Creates a simulated pharmacy order with atomic branch-stock reservation "
    "and format-only academic prescription validation."
)
CREATE_ORDER_INPUT_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "branch_id": {
            "type": "string",
            "enum": sorted(EXPECTED_BRANCH_IDS),
        },
        "items": {
            "type": "array",
            "minItems": 1,
            "maxItems": 50,
            "items": {
                "type": "object",
                "properties": {
                    "sku": {
                        "type": "string",
                        "minLength": 1,
                        "pattern": "^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$",
                    },
                    "quantity": {"type": "integer", "minimum": 1},
                },
                "required": ["sku", "quantity"],
                "additionalProperties": False,
            },
        },
        "prescription_id": {
            "type": "string",
            "minLength": 4,
            "maxLength": 64,
            "pattern": "^RX-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$",
        },
    },
    "required": ["branch_id", "items"],
    "additionalProperties": False,
}

GET_ORDER_STATUS_NAME = "get_order_status"
GET_ORDER_STATUS_DESCRIPTION = (
    "Returns the current status and immutable details of a simulated order."
)
GET_ORDER_STATUS_INPUT_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "order_id": {
            "type": "string",
            "minLength": 1,
            "pattern": "^ORD-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$",
        }
    },
    "required": ["order_id"],
    "additionalProperties": False,
}

ORDER_DISCLAIMER = (
    "Academic simulation only: this is not a real purchase, prescription "
    "validation, or guarantee that a medication is appropriate or safe."
)

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")
_PRESCRIPTION_PATTERN = re.compile(r"^RX-[A-Z0-9]+(?:-[A-Z0-9]+)*$")


@dataclass(frozen=True, slots=True, kw_only=True)
class PharmacyOrderHandlers:
    """Handlers enlazados al mismo estado SQLite consultado por stock."""

    catalog: PharmacyCatalog
    store: SQLitePharmacyStore

    def __post_init__(self) -> None:
        if not isinstance(self.catalog, PharmacyCatalog):
            raise TypeError("'catalog' must be a PharmacyCatalog instance.")
        if not isinstance(self.store, SQLitePharmacyStore):
            raise TypeError("'store' must be a SQLitePharmacyStore instance.")

    def create_order(self, arguments: ToolArguments) -> JsonValue:
        _reject_unexpected_arguments(
            arguments,
            {"branch_id", "items", "prescription_id"},
        )
        branch_id = _required_identifier(arguments, "branch_id").casefold()
        items = _required_order_items(arguments)
        prescription_id = _optional_prescription_id(arguments)

        try:
            order = self.store.create_order(
                branch_id=branch_id,
                items=items,
                prescription_id=prescription_id,
            )
        except OrderValidationError as exc:
            raise InvalidParamsError(str(exc)) from exc
        except OrderExecutionError as exc:
            return _tool_error(f"{exc} {ORDER_DISCLAIMER}")

        structured = self._serialize_order(order)
        text = (
            f"Created simulated order {order.order_id} with status "
            f"'{order.status}' at {structured['branch_name']}; total "
            f"{CATALOG_CURRENCY} {structured['total']['amount']}. "
            f"{ORDER_DISCLAIMER}"
        )
        return _tool_result(text, {"order": structured})

    def get_order_status(self, arguments: ToolArguments) -> JsonValue:
        _reject_unexpected_arguments(arguments, {"order_id"})
        order_id = _required_identifier(arguments, "order_id").upper()

        try:
            order = self.store.get_order(order_id)
        except OrderLookupError as exc:
            return _tool_error(f"{exc} {ORDER_DISCLAIMER}")

        structured = self._serialize_order(order)
        text = (
            f"Simulated order {order.order_id} has status '{order.status}' "
            f"and total {CATALOG_CURRENCY} "
            f"{structured['total']['amount']}. {ORDER_DISCLAIMER}"
        )
        return _tool_result(text, {"order": structured})

    def _serialize_order(self, order: OrderRecord) -> dict[str, JsonValue]:
        branch = self.catalog.get_branch(order.branch_id)
        if branch is None:
            raise RuntimeError("An order references an unknown branch.")

        serialized_items: list[dict[str, JsonValue]] = []
        for item in order.items:
            medication = self.catalog.get_medication(item.sku)
            if medication is None:
                raise RuntimeError("An order references an unknown medication.")
            serialized_items.append(
                {
                    "sku": item.sku,
                    "medication_name": medication.name,
                    "quantity": item.quantity,
                    "unit_price": _money(item.unit_price_centavos),
                    "line_total": _money(item.line_total_centavos),
                }
            )

        return {
            "order_id": order.order_id,
            "status": order.status,
            "branch_id": order.branch_id,
            "branch_name": branch.name,
            "items": serialized_items,
            "total": _money(order.total_centavos),
            "prescription_required": order.prescription_required,
            "prescription_reference_provided": order.prescription_provided,
            "prescription_validation_scope": "format_only_simulation",
            "created_at": order.created_at,
            "simulated": True,
            "disclaimer": ORDER_DISCLAIMER,
        }


def _required_order_items(arguments: ToolArguments) -> tuple[OrderItemRequest, ...]:
    value = arguments.get("items")
    if not isinstance(value, list):
        raise InvalidParamsError("'items' must be an array.")
    if not value:
        raise InvalidParamsError("'items' must contain at least one item.")
    if len(value) > 50:
        raise InvalidParamsError("'items' must contain at most 50 items.")

    items: list[OrderItemRequest] = []
    seen_skus: set[str] = set()
    for index, raw_item in enumerate(value):
        if not isinstance(raw_item, dict):
            raise InvalidParamsError(f"'items[{index}]' must be an object.")
        unexpected = sorted(set(raw_item) - {"sku", "quantity"})
        if unexpected:
            raise InvalidParamsError(
                f"Unexpected fields in 'items[{index}]': "
                + ", ".join(unexpected)
                + "."
            )
        if "sku" not in raw_item or "quantity" not in raw_item:
            raise InvalidParamsError(
                f"'items[{index}]' requires 'sku' and 'quantity'."
            )

        sku_value = raw_item["sku"]
        if not isinstance(sku_value, str) or not sku_value.strip():
            raise InvalidParamsError(
                f"'items[{index}].sku' must be a non-empty string."
            )
        sku = sku_value.strip()
        if _IDENTIFIER_PATTERN.fullmatch(sku) is None:
            raise InvalidParamsError(
                f"'items[{index}].sku' must use letters, digits, and "
                "single hyphen separators."
            )
        sku = sku.upper()
        if sku in seen_skus:
            raise InvalidParamsError("'items' must not repeat a SKU.")

        quantity = raw_item["quantity"]
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise InvalidParamsError(
                f"'items[{index}].quantity' must be an integer."
            )
        if quantity <= 0:
            raise InvalidParamsError(
                f"'items[{index}].quantity' must be positive."
            )

        items.append(OrderItemRequest(sku=sku, quantity=quantity))
        seen_skus.add(sku)

    return tuple(items)


def _optional_prescription_id(arguments: ToolArguments) -> str | None:
    if "prescription_id" not in arguments:
        return None
    value = arguments["prescription_id"]
    if not isinstance(value, str) or not value.strip():
        raise InvalidParamsError("'prescription_id' must be a non-empty string.")
    normalized = value.strip().upper()
    if len(normalized) > 64 or _PRESCRIPTION_PATTERN.fullmatch(normalized) is None:
        raise InvalidParamsError(
            "'prescription_id' must use the simulated format 'RX-...'."
        )
    return normalized


def _required_identifier(arguments: ToolArguments, name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise InvalidParamsError(f"'{name}' must be a non-empty string.")
    identifier = value.strip()
    if _IDENTIFIER_PATTERN.fullmatch(identifier) is None:
        raise InvalidParamsError(
            f"'{name}' must use letters, digits, and single hyphen separators."
        )
    return identifier


def _reject_unexpected_arguments(
    arguments: ToolArguments,
    allowed: set[str],
) -> None:
    unexpected = sorted(set(arguments) - allowed)
    if unexpected:
        raise InvalidParamsError(
            "Unexpected tool arguments: " + ", ".join(unexpected) + "."
        )


def _money(amount_centavos: int) -> dict[str, JsonValue]:
    quetzales, centavos = divmod(amount_centavos, 100)
    return {
        "amount": f"{quetzales}.{centavos:02d}",
        "currency": CATALOG_CURRENCY,
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
