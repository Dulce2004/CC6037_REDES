"""Dominio educativo y datos simulados del caso de uso de farmacia."""

from .assessment import (
    ASSESSMENT_DISCLAIMER,
    SymptomAssessmentValidationError,
    assess_symptoms,
)
from .catalog import (
    EXPECTED_BRANCH_IDS,
    CatalogQueryError,
    CatalogValidationError,
    PharmacyCatalog,
    load_default_catalog,
)
from .classifier import SymptomValidationError, classify_symptoms
from .inventory import (
    InventoryLookupError,
    InventoryRepository,
    InventoryValidationError,
    load_default_inventory,
)
from .interactions import (
    DEFAULT_INTERACTIONS_PATH,
    INTERACTION_DISCLAIMER,
    AllergyRule,
    InteractionAlert,
    InteractionLookupError,
    InteractionQueryError,
    InteractionRepository,
    InteractionValidationError,
    MedicationInteractionRule,
    load_default_interactions,
)
from .models import CATALOG_CURRENCY, Branch, InventoryRecord, Medication, Money
from .symptoms import (
    CATEGORY_SYMPTOMS,
    MINIMUM_MATCHING_SYMPTOMS,
    RECOGNIZED_SYMPTOMS,
)

__all__ = [
    "ASSESSMENT_DISCLAIMER",
    "CATALOG_CURRENCY",
    "CATEGORY_SYMPTOMS",
    "DEFAULT_INTERACTIONS_PATH",
    "EXPECTED_BRANCH_IDS",
    "INTERACTION_DISCLAIMER",
    "MINIMUM_MATCHING_SYMPTOMS",
    "RECOGNIZED_SYMPTOMS",
    "AllergyRule",
    "Branch",
    "CatalogQueryError",
    "CatalogValidationError",
    "InteractionAlert",
    "InteractionLookupError",
    "InteractionQueryError",
    "InteractionRepository",
    "InteractionValidationError",
    "InventoryLookupError",
    "InventoryRecord",
    "InventoryRepository",
    "InventoryValidationError",
    "Medication",
    "MedicationInteractionRule",
    "Money",
    "PharmacyCatalog",
    "SymptomAssessmentValidationError",
    "SymptomValidationError",
    "assess_symptoms",
    "classify_symptoms",
    "load_default_catalog",
    "load_default_interactions",
    "load_default_inventory",
]
