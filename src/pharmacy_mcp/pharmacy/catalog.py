"""Carga y consulta de solo lectura para el catálogo simulado."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Callable, Iterable
from pathlib import Path
from types import MappingProxyType
from typing import TypeVar

from .models import Branch, Medication, Money

EXPECTED_BRANCH_IDS = frozenset({"zona-5", "zona-15", "mixco"})
DEFAULT_DATA_DIRECTORY = Path(__file__).with_name("data")
_CatalogItem = TypeVar("_CatalogItem")


class CatalogValidationError(ValueError):
    """Indica que los datos externos del catálogo son inválidos o inconsistentes."""


class CatalogQueryError(ValueError):
    """Indica que una consulta del catálogo no tiene parámetros válidos."""


class PharmacyCatalog:
    """Repositorio inmutable de sucursales y medicamentos del proyecto."""

    def __init__(
        self,
        *,
        branches: Iterable[Branch],
        medications: Iterable[Medication],
    ) -> None:
        branch_list = tuple(branches)
        medication_list = tuple(medications)

        self._branches = MappingProxyType(
            _index_unique(
                branch_list,
                key=lambda branch: branch.branch_id,
                label="branch ID",
            )
        )
        _validate_unique_text(
            (branch.name for branch in branch_list),
            label="branch name",
        )
        if set(self._branches) != EXPECTED_BRANCH_IDS:
            raise CatalogValidationError(
                "Catalog branches must be exactly: "
                + ", ".join(sorted(EXPECTED_BRANCH_IDS))
                + "."
            )

        self._medications = MappingProxyType(
            _index_unique(
                medication_list,
                key=lambda medication: medication.sku,
                label="medication SKU",
            )
        )
        _validate_unique_text(
            (medication.name for medication in medication_list),
            label="medication name",
        )

    @classmethod
    def from_directory(cls, data_directory: str | Path) -> PharmacyCatalog:
        """Carga ``branches.json`` y ``medications.json`` desde un directorio."""

        directory = Path(data_directory)
        branches_data = _read_json_array(directory / "branches.json")
        medications_data = _read_json_array(directory / "medications.json")

        try:
            branches = tuple(_parse_branch(item) for item in branches_data)
            medications = tuple(
                _parse_medication(item) for item in medications_data
            )
            return cls(branches=branches, medications=medications)
        except CatalogValidationError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise CatalogValidationError(f"Invalid catalog data: {exc}") from exc

    def get_branch(self, branch_id: str) -> Branch | None:
        return self._branches.get(branch_id)

    def list_branches(self) -> tuple[Branch, ...]:
        return tuple(self._branches.values())

    def get_medication(self, sku: str) -> Medication | None:
        return self._medications.get(sku)

    def list_medications(self) -> tuple[Medication, ...]:
        return tuple(self._medications.values())

    def search_medications(
        self,
        query: str,
        *,
        otc_only: bool = False,
    ) -> tuple[Medication, ...]:
        """Busca texto en los campos públicos del medicamento y conserva el orden."""

        if not isinstance(query, str) or not query.strip():
            raise CatalogQueryError("'query' must be a non-empty string.")
        if not isinstance(otc_only, bool):
            raise CatalogQueryError("'otc_only' must be a boolean.")

        normalized_query = _normalize_search_text(query)
        if not normalized_query:
            raise CatalogQueryError(
                "'query' must contain searchable letters or numbers."
            )
        matches: list[Medication] = []
        for medication in self._medications.values():
            if otc_only and medication.requires_prescription:
                continue

            searchable_values = (
                medication.sku,
                medication.name,
                *medication.aliases,
                medication.active_ingredient,
                medication.therapeutic_category,
            )
            if any(
                normalized_query in _normalize_search_text(value)
                for value in searchable_values
            ):
                matches.append(medication)

        return tuple(matches)


def load_default_catalog() -> PharmacyCatalog:
    """Carga los datos incluidos en el paquete de la aplicación."""

    return PharmacyCatalog.from_directory(DEFAULT_DATA_DIRECTORY)


def _read_json_array(path: Path) -> list[object]:
    try:
        raw_data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CatalogValidationError(
            f"Cannot read catalog file '{path.name}'."
        ) from exc
    except json.JSONDecodeError as exc:
        raise CatalogValidationError(
            f"Catalog file '{path.name}' does not contain valid JSON."
        ) from exc

    if not isinstance(raw_data, list):
        raise CatalogValidationError(
            f"Catalog file '{path.name}' must contain a JSON array."
        )
    if not raw_data:
        raise CatalogValidationError(
            f"Catalog file '{path.name}' must not be empty."
        )
    return raw_data


def _parse_branch(value: object) -> Branch:
    item = _object_with_exact_keys(value, {"branch_id", "name"}, "branch")
    return Branch(branch_id=item["branch_id"], name=item["name"])


def _parse_medication(value: object) -> Medication:
    expected_keys = {
        "sku",
        "name",
        "aliases",
        "active_ingredient",
        "therapeutic_category",
        "dosage_information",
        "contraindications",
        "requires_prescription",
        "price",
    }
    item = _object_with_exact_keys(value, expected_keys, "medication")
    price_data = _object_with_exact_keys(
        item["price"], {"amount_centavos", "currency"}, "price"
    )

    return Medication(
        sku=item["sku"],
        name=item["name"],
        aliases=_string_tuple(item["aliases"], "aliases"),
        active_ingredient=item["active_ingredient"],
        therapeutic_category=item["therapeutic_category"],
        dosage_information=item["dosage_information"],
        contraindications=_string_tuple(
            item["contraindications"], "contraindications"
        ),
        requires_prescription=item["requires_prescription"],
        price=Money(
            amount_centavos=price_data["amount_centavos"],
            currency=price_data["currency"],
        ),
    )


def _object_with_exact_keys(
    value: object,
    expected_keys: set[str],
    label: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise CatalogValidationError(f"Every {label} must be a JSON object.")

    actual_keys = set(value)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        unexpected = sorted(actual_keys - expected_keys)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise CatalogValidationError(
            f"Invalid {label} fields: {'; '.join(details)}."
        )
    return value


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CatalogValidationError(f"'{field_name}' must be a JSON array.")
    if not all(isinstance(item, str) for item in value):
        raise CatalogValidationError(f"'{field_name}' must contain only strings.")
    return tuple(value)


def _index_unique(
    items: Iterable[_CatalogItem],
    *,
    key: Callable[[_CatalogItem], str],
    label: str,
) -> dict[str, _CatalogItem]:
    indexed: dict[str, _CatalogItem] = {}
    for item in items:
        item_key = key(item)
        if item_key in indexed:
            raise CatalogValidationError(f"Duplicate {label}: '{item_key}'.")
        indexed[item_key] = item
    return indexed


def _validate_unique_text(values: Iterable[str], *, label: str) -> None:
    seen: set[str] = set()
    for value in values:
        normalized = value.casefold()
        if normalized in seen:
            raise CatalogValidationError(f"Duplicate {label}: '{value}'.")
        seen.add(normalized)


def _normalize_search_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_accents = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )
    words = without_accents.replace("_", " ").replace("-", " ").split()
    return " ".join(words)
