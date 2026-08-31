"""Modelos inmutables para el catálogo simulado de farmacia."""

from __future__ import annotations

import re
from dataclasses import dataclass

CATALOG_CURRENCY = "GTQ"
_SKU_FORMAT = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$")


def _validate_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{field_name}' must be a non-empty string.")
    if value != value.strip():
        raise ValueError(f"'{field_name}' must not have surrounding whitespace.")


def _validate_text_tuple(values: object, field_name: str) -> None:
    if not isinstance(values, tuple):
        raise ValueError(f"'{field_name}' must be a tuple of strings.")
    if not values:
        raise ValueError(f"'{field_name}' must not be empty.")

    normalized_values: set[str] = set()
    for value in values:
        _validate_text(value, field_name)
        normalized = value.casefold()
        if normalized in normalized_values:
            raise ValueError(f"'{field_name}' must not contain duplicates.")
        normalized_values.add(normalized)


@dataclass(frozen=True, slots=True, kw_only=True)
class Money:
    """Cantidad monetaria exacta representada en centavos, nunca como ``float``."""

    amount_centavos: int
    currency: str = CATALOG_CURRENCY

    def __post_init__(self) -> None:
        if isinstance(self.amount_centavos, bool) or not isinstance(
            self.amount_centavos, int
        ):
            raise ValueError("'amount_centavos' must be an integer.")
        if self.amount_centavos <= 0:
            raise ValueError("'amount_centavos' must be positive.")
        if self.currency != CATALOG_CURRENCY:
            raise ValueError(f"Catalog prices must use {CATALOG_CURRENCY}.")

    @property
    def amount(self) -> str:
        """Devuelve el valor decimal exacto listo para serializar o mostrar."""

        quetzales, centavos = divmod(self.amount_centavos, 100)
        return f"{quetzales}.{centavos:02d}"

    def to_dict(self) -> dict[str, str]:
        return {"amount": self.amount, "currency": self.currency}


@dataclass(frozen=True, slots=True, kw_only=True)
class Branch:
    """Sucursal de la cadena identificada por un ID estable."""

    branch_id: str
    name: str

    def __post_init__(self) -> None:
        _validate_text(self.branch_id, "branch_id")
        _validate_text(self.name, "name")


@dataclass(frozen=True, slots=True, kw_only=True)
class Medication:
    """Información de catálogo; no representa una prescripción personalizada."""

    sku: str
    name: str
    aliases: tuple[str, ...]
    active_ingredient: str
    therapeutic_category: str
    dosage_information: str
    contraindications: tuple[str, ...]
    requires_prescription: bool
    price: Money

    def __post_init__(self) -> None:
        _validate_text(self.sku, "sku")
        if not _SKU_FORMAT.fullmatch(self.sku):
            raise ValueError("'sku' must use uppercase letters, numbers, and hyphens.")

        for field_name in (
            "name",
            "active_ingredient",
            "therapeutic_category",
            "dosage_information",
        ):
            _validate_text(getattr(self, field_name), field_name)

        _validate_text_tuple(self.aliases, "aliases")
        _validate_text_tuple(self.contraindications, "contraindications")
        if self.name.casefold() in {alias.casefold() for alias in self.aliases}:
            raise ValueError("'aliases' must not repeat the medication name.")
        if not isinstance(self.requires_prescription, bool):
            raise ValueError("'requires_prescription' must be a boolean.")
        if not isinstance(self.price, Money):
            raise ValueError("'price' must be a Money instance.")
