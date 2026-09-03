"""Repositorio inmutable de interacciones y alergias simuladas."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from .catalog import PharmacyCatalog, load_default_catalog

DEFAULT_INTERACTIONS_PATH = Path(__file__).with_name("data") / "interactions.json"
INTERACTION_DISCLAIMER = (
    "Controlled simulated data only; this check is not exhaustive, does not "
    "guarantee safety, and does not replace a pharmacist or prescriber."
)
_SKU_FORMAT = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$")
_ALLOWED_SEVERITIES = frozenset({"moderate", "high"})


class InteractionValidationError(ValueError):
    """Indica que el conjunto controlado de reglas es inconsistente."""


class InteractionQueryError(ValueError):
    """Indica que los argumentos de una consulta no son válidos."""


class InteractionLookupError(LookupError):
    """Indica que una consulta referencia un SKU inexistente."""


@dataclass(frozen=True, slots=True, kw_only=True)
class MedicationInteractionRule:
    """Regla simulada entre exactamente dos medicamentos del catálogo."""

    skus: tuple[str, str]
    severity: str
    message: str

    def __post_init__(self) -> None:
        if len(self.skus) != 2 or self.skus[0] == self.skus[1]:
            raise InteractionValidationError(
                "An interaction rule must contain two different SKUs."
            )
        if any(_SKU_FORMAT.fullmatch(sku) is None for sku in self.skus):
            raise InteractionValidationError(
                "Interaction SKUs must use uppercase letters, numbers, and hyphens."
            )
        if self.severity not in _ALLOWED_SEVERITIES:
            raise InteractionValidationError(
                "Interaction severity must be 'moderate' or 'high'."
            )
        _require_text(self.message, "message")


@dataclass(frozen=True, slots=True, kw_only=True)
class AllergyRule:
    """Términos simulados de alergia asociados a un SKU."""

    sku: str
    terms: tuple[str, ...]

    def __post_init__(self) -> None:
        if _SKU_FORMAT.fullmatch(self.sku) is None:
            raise InteractionValidationError(
                "Allergy-rule SKUs must use uppercase letters, numbers, and hyphens."
            )
        if not self.terms:
            raise InteractionValidationError(
                "An allergy rule must contain at least one term."
            )
        normalized_terms = tuple(_normalize_text(term) for term in self.terms)
        if any(not term for term in normalized_terms):
            raise InteractionValidationError(
                "Allergy terms must contain searchable text."
            )
        if len(set(normalized_terms)) != len(normalized_terms):
            raise InteractionValidationError(
                "Allergy terms must be unique after normalization."
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class InteractionAlert:
    """Hallazgo controlado serializable por el adaptador MCP."""

    alert_type: str
    severity: str
    message: str
    related_sku: str | None = None
    allergy: str | None = None

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "type": self.alert_type,
            "severity": self.severity,
            "message": self.message,
        }
        if self.related_sku is not None:
            result["related_sku"] = self.related_sku
        if self.allergy is not None:
            result["allergy"] = self.allergy
        return result


class InteractionRepository:
    """Consulta reglas simuladas sin permitir mutaciones."""

    def __init__(
        self,
        *,
        catalog: PharmacyCatalog,
        medication_rules: Iterable[MedicationInteractionRule],
        allergy_rules: Iterable[AllergyRule],
    ) -> None:
        if not isinstance(catalog, PharmacyCatalog):
            raise InteractionValidationError(
                "'catalog' must be a PharmacyCatalog instance."
            )

        interaction_index: dict[frozenset[str], MedicationInteractionRule] = {}
        for rule in medication_rules:
            if not isinstance(rule, MedicationInteractionRule):
                raise InteractionValidationError(
                    "Every medication rule must be a MedicationInteractionRule."
                )
            for sku in rule.skus:
                if catalog.get_medication(sku) is None:
                    raise InteractionValidationError(
                        f"Unknown interaction-rule SKU: '{sku}'."
                    )
            key = frozenset(rule.skus)
            if key in interaction_index:
                raise InteractionValidationError(
                    "Duplicate medication interaction pair."
                )
            interaction_index[key] = rule

        allergy_index: dict[str, AllergyRule] = {}
        for rule in allergy_rules:
            if not isinstance(rule, AllergyRule):
                raise InteractionValidationError(
                    "Every allergy rule must be an AllergyRule."
                )
            if catalog.get_medication(rule.sku) is None:
                raise InteractionValidationError(
                    f"Unknown allergy-rule SKU: '{rule.sku}'."
                )
            if rule.sku in allergy_index:
                raise InteractionValidationError(
                    f"Duplicate allergy rule for SKU: '{rule.sku}'."
                )
            allergy_index[rule.sku] = rule

        catalog_skus = {
            medication.sku for medication in catalog.list_medications()
        }
        if set(allergy_index) != catalog_skus:
            raise InteractionValidationError(
                "Allergy rules must cover every medication in the catalog."
            )

        self._catalog = catalog
        self._medication_rules = MappingProxyType(interaction_index)
        self._allergy_rules = MappingProxyType(allergy_index)

    @classmethod
    def from_file(
        cls,
        *,
        catalog: PharmacyCatalog,
        interactions_path: str | Path,
    ) -> InteractionRepository:
        """Carga y valida reglas simuladas desde un objeto JSON."""

        raw_data = _read_interaction_data(Path(interactions_path))
        try:
            medication_rules = tuple(
                _parse_medication_rule(item)
                for item in raw_data["medication_interactions"]
            )
            allergy_rules = tuple(
                _parse_allergy_rule(item) for item in raw_data["allergy_rules"]
            )
            return cls(
                catalog=catalog,
                medication_rules=medication_rules,
                allergy_rules=allergy_rules,
            )
        except InteractionValidationError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise InteractionValidationError(
                f"Invalid interaction data: {exc}"
            ) from exc

    def check_interactions(
        self,
        medication_sku: object,
        current_medications: object,
        allergies: object,
    ) -> tuple[InteractionAlert, ...]:
        """Devuelve únicamente alertas presentes en el conjunto controlado."""

        requested_sku = _normalize_query_sku(medication_sku)
        current_skus = _normalize_current_medications(current_medications)
        allergy_values = _normalize_allergies(allergies)

        self._require_catalog_sku(requested_sku)
        for sku in current_skus:
            self._require_catalog_sku(sku)
        if requested_sku in current_skus:
            raise InteractionQueryError(
                "'current_medications' must not repeat 'medication_sku'."
            )

        alerts: list[InteractionAlert] = []
        for current_sku in current_skus:
            rule = self._medication_rules.get(
                frozenset((requested_sku, current_sku))
            )
            if rule is not None:
                alerts.append(
                    InteractionAlert(
                        alert_type="medication_interaction",
                        severity=rule.severity,
                        message=rule.message,
                        related_sku=current_sku,
                    )
                )

        requested_allergy_rule = self._allergy_rules[requested_sku]
        normalized_terms = tuple(
            _normalize_text(term) for term in requested_allergy_rule.terms
        )
        for original_allergy, normalized_allergy in allergy_values:
            if any(
                _phrases_overlap(normalized_allergy, term)
                for term in normalized_terms
            ):
                alerts.append(
                    InteractionAlert(
                        alert_type="allergy_alert",
                        severity="high",
                        message=(
                            "A recorded allergy matches a controlled term for "
                            "the requested medication and requires professional "
                            "review."
                        ),
                        allergy=original_allergy,
                    )
                )

        return tuple(alerts)

    def list_medication_rules(self) -> tuple[MedicationInteractionRule, ...]:
        return tuple(self._medication_rules.values())

    def list_allergy_rules(self) -> tuple[AllergyRule, ...]:
        return tuple(self._allergy_rules.values())

    def _require_catalog_sku(self, sku: str) -> None:
        if self._catalog.get_medication(sku) is None:
            raise InteractionLookupError(f"Unknown medication SKU: '{sku}'.")


def load_default_interactions(
    catalog: PharmacyCatalog | None = None,
) -> InteractionRepository:
    """Carga las reglas incluidas con el proyecto."""

    selected_catalog = catalog if catalog is not None else load_default_catalog()
    return InteractionRepository.from_file(
        catalog=selected_catalog,
        interactions_path=DEFAULT_INTERACTIONS_PATH,
    )


def _read_interaction_data(path: Path) -> dict[str, list[object]]:
    try:
        raw_data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise InteractionValidationError(
            f"Cannot read interaction file '{path.name}'."
        ) from exc
    except json.JSONDecodeError as exc:
        raise InteractionValidationError(
            f"Interaction file '{path.name}' does not contain valid JSON."
        ) from exc

    if not isinstance(raw_data, dict) or set(raw_data) != {
        "medication_interactions",
        "allergy_rules",
    }:
        raise InteractionValidationError(
            "Interaction data must contain only medication_interactions and "
            "allergy_rules."
        )
    if not isinstance(raw_data["medication_interactions"], list):
        raise InteractionValidationError(
            "'medication_interactions' must be a JSON array."
        )
    if not isinstance(raw_data["allergy_rules"], list):
        raise InteractionValidationError("'allergy_rules' must be a JSON array.")
    return raw_data


def _parse_medication_rule(value: object) -> MedicationInteractionRule:
    item = _object_with_exact_keys(
        value,
        {"skus", "severity", "message"},
        "medication interaction",
    )
    skus = item["skus"]
    if not isinstance(skus, list) or len(skus) != 2:
        raise InteractionValidationError(
            "'skus' must be a JSON array containing exactly two values."
        )
    return MedicationInteractionRule(
        skus=(skus[0], skus[1]),
        severity=item["severity"],
        message=item["message"],
    )


def _parse_allergy_rule(value: object) -> AllergyRule:
    item = _object_with_exact_keys(value, {"sku", "terms"}, "allergy rule")
    terms = item["terms"]
    if not isinstance(terms, list):
        raise InteractionValidationError("'terms' must be a JSON array.")
    return AllergyRule(sku=item["sku"], terms=tuple(terms))


def _object_with_exact_keys(
    value: object,
    expected_keys: set[str],
    label: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise InteractionValidationError(
            f"Every {label} must contain exactly: "
            + ", ".join(sorted(expected_keys))
            + "."
        )
    return value


def _normalize_query_sku(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InteractionQueryError("Medication SKUs must be non-empty strings.")
    sku = value.strip().upper()
    if _SKU_FORMAT.fullmatch(sku) is None:
        raise InteractionQueryError(
            "Medication SKUs must use letters, numbers, and single hyphen separators."
        )
    return sku


def _normalize_current_medications(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise InteractionQueryError("'current_medications' must be an array.")
    normalized = tuple(_normalize_query_sku(sku) for sku in value)
    if len(set(normalized)) != len(normalized):
        raise InteractionQueryError(
            "'current_medications' must not contain duplicate SKUs."
        )
    return normalized


def _normalize_allergies(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise InteractionQueryError("'allergies' must be an array.")

    normalized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for allergy in value:
        if not isinstance(allergy, str) or not allergy.strip():
            raise InteractionQueryError(
                "Every allergy must be a non-empty string."
            )
        original = allergy.strip()
        searchable = _normalize_text(original)
        if not searchable:
            raise InteractionQueryError(
                "Every allergy must contain searchable letters or numbers."
            )
        if searchable in seen:
            raise InteractionQueryError(
                "'allergies' must not contain duplicate values."
            )
        normalized.append((original, searchable))
        seen.add(searchable)
    return tuple(normalized)


def _normalize_text(value: object) -> str:
    _require_text(value, "text")
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_accents = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )
    return " ".join(re.sub(r"[^a-z0-9]+", " ", without_accents).split())


def _phrases_overlap(first: str, second: str) -> bool:
    padded_first = f" {first} "
    padded_second = f" {second} "
    return (
        first == second
        or padded_first in padded_second
        or padded_second in padded_first
    )


def _require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InteractionValidationError(
            f"'{field_name}' must be a non-empty string."
        )
