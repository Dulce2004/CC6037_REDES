"""Catálogo y clasificación educativa del caso de uso de farmacia."""

from .classifier import SymptomValidationError, classify_symptoms
from .symptoms import (
    CATEGORY_SYMPTOMS,
    MINIMUM_MATCHING_SYMPTOMS,
    RECOGNIZED_SYMPTOMS,
)

__all__ = [
    "CATEGORY_SYMPTOMS",
    "MINIMUM_MATCHING_SYMPTOMS",
    "RECOGNIZED_SYMPTOMS",
    "SymptomValidationError",
    "classify_symptoms",
]
