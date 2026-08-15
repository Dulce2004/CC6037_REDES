"""Clasificador determinista para el caso de uso educativo de farmacia."""

from __future__ import annotations

import re

from .symptoms import (
    CATEGORY_SYMPTOMS,
    MINIMUM_MATCHING_SYMPTOMS,
    RECOGNIZED_SYMPTOMS,
)

_SYMPTOM_FORMAT = re.compile(r"^[a-z]+(?:_[a-z]+)*$")


class SymptomValidationError(ValueError):
    """Indica que la entrada no cumple el catálogo controlado de síntomas."""


def classify_symptoms(symptoms: object) -> dict[str, object]:
    """Valida y clasifica una lista según reglas educativas transparentes."""

    normalized_symptoms = _normalize_symptoms(symptoms)
    matches = {
        category: [
            symptom
            for symptom in normalized_symptoms
            if symptom in category_symptoms
        ]
        for category, category_symptoms in CATEGORY_SYMPTOMS.items()
    }
    qualifying_matches = {
        category: matched
        for category, matched in matches.items()
        if len(matched) >= MINIMUM_MATCHING_SYMPTOMS
    }

    if not qualifying_matches:
        return _unclassified_result()

    highest_score = max(len(matched) for matched in qualifying_matches.values())
    best_categories = [
        category
        for category, matched in qualifying_matches.items()
        if len(matched) == highest_score
    ]
    if len(best_categories) != 1:
        return _unclassified_result(
            "More than one supported category matches with the same score."
        )

    category = best_categories[0]
    matched_symptoms = qualifying_matches[category]
    return {
        "status": "classified",
        "category": category,
        "matchedSymptoms": matched_symptoms,
        "message": f"Symptoms match the {category} category.",
    }


def _normalize_symptoms(symptoms: object) -> list[str]:
    if not isinstance(symptoms, list):
        raise SymptomValidationError("'symptoms' must be an array.")
    if not symptoms:
        raise SymptomValidationError("'symptoms' must not be empty.")

    normalized: list[str] = []
    seen: set[str] = set()
    for symptom in symptoms:
        if not isinstance(symptom, str):
            raise SymptomValidationError("Every symptom must be a string.")

        identifier = symptom.strip().lower()
        if not identifier:
            raise SymptomValidationError("Symptom identifiers must not be empty.")
        if not _SYMPTOM_FORMAT.fullmatch(identifier):
            raise SymptomValidationError(
                f"Invalid symptom identifier format: '{identifier}'."
            )
        if identifier not in RECOGNIZED_SYMPTOMS:
            raise SymptomValidationError(f"Unknown symptom: '{identifier}'.")
        if identifier not in seen:
            normalized.append(identifier)
            seen.add(identifier)

    return normalized


def _unclassified_result(message: str | None = None) -> dict[str, object]:
    return {
        "status": "unclassified",
        "category": None,
        "matchedSymptoms": [],
        "message": message
        or "No supported category matches the provided symptoms.",
    }
