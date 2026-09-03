"""Evaluación educativa de texto de síntomas mediante reglas controladas."""

from __future__ import annotations

import re
import unicodedata

from .classifier import classify_symptoms

ASSESSMENT_DISCLAIMER = (
    "Academic simulated assessment; not a diagnosis, medical advice, or "
    "substitute for a qualified healthcare professional."
)

_MAX_SYMPTOM_TEXT_LENGTH = 1000
_MAX_AGE = 120
_MAX_DURATION_DAYS = 365

_SYMPTOM_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("fever", ("fiebre", "fever")),
    ("cough", ("tos", "cough")),
    (
        "sore_throat",
        ("dolor de garganta", "garganta irritada", "sore throat"),
    ),
    (
        "nasal_congestion",
        ("congestion nasal", "nariz tapada", "nasal congestion"),
    ),
    ("sneezing", ("estornudo", "estornudos", "sneezing")),
    (
        "itchy_eyes",
        ("picazon en los ojos", "ojos con picazon", "itchy eyes"),
    ),
    ("nausea", ("nausea", "nauseas")),
    ("diarrhea", ("diarrea", "diarrhea")),
    (
        "abdominal_pain",
        ("dolor abdominal", "dolor de estomago", "abdominal pain"),
    ),
)

_URGENT_RED_FLAG_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "difficulty_breathing",
        (
            "dificultad para respirar",
            "no puedo respirar",
            "falta de aire intensa",
            "difficulty breathing",
            "cannot breathe",
        ),
    ),
    (
        "chest_pain",
        ("dolor de pecho", "dolor en el pecho", "chest pain"),
    ),
    (
        "confusion",
        ("confusion", "desorientacion", "confused", "disoriented"),
    ),
    ("fainting", ("desmayo", "desmayado", "fainting", "fainted")),
    (
        "severe_bleeding",
        (
            "sangrado abundante",
            "hemorragia",
            "severe bleeding",
            "heavy bleeding",
        ),
    ),
    (
        "blue_lips",
        ("labios morados", "labios azules", "blue lips"),
    ),
    (
        "face_or_throat_swelling",
        (
            "hinchazon de la cara",
            "hinchazon de garganta",
            "cara hinchada",
            "swollen face",
            "throat swelling",
        ),
    ),
)


class SymptomAssessmentValidationError(ValueError):
    """Indica que los parámetros de una evaluación no son válidos."""


def assess_symptoms(
    symptoms: object,
    *,
    age: object = None,
    duration_days: object = None,
) -> dict[str, object]:
    """Evalúa texto natural con reglas simuladas sin emitir un diagnóstico."""

    symptom_text = _validate_symptom_text(symptoms)
    validated_age = _validate_optional_integer(age, "age", _MAX_AGE)
    validated_duration = _validate_optional_integer(
        duration_days,
        "duration_days",
        _MAX_DURATION_DAYS,
    )

    normalized_text = _normalize_text(symptom_text)
    recognized = _find_matches(normalized_text, _SYMPTOM_PHRASES)
    red_flags = _find_matches(normalized_text, _URGENT_RED_FLAG_PHRASES)

    if recognized:
        classification = classify_symptoms(recognized)
        category = classification["category"]
        matched = classification["matchedSymptoms"]
    else:
        category = None
        matched = []

    severity, reasons = _determine_severity(
        recognized=recognized,
        red_flags=red_flags,
        category=category,
        age=validated_age,
        duration_days=validated_duration,
    )
    action = _recommended_action(severity)

    return {
        "severity": severity,
        "category": category,
        "recognized_symptoms": recognized,
        "matched_symptoms": matched,
        "red_flags": red_flags,
        "reasons": reasons,
        "age": validated_age,
        "duration_days": validated_duration,
        "recommended_action": action,
        "medication_purchase_recommended": False,
        "disclaimer": ASSESSMENT_DISCLAIMER,
    }


def _validate_symptom_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SymptomAssessmentValidationError(
            "'symptoms' must be a non-empty string."
        )
    text = value.strip()
    if len(text) > _MAX_SYMPTOM_TEXT_LENGTH:
        raise SymptomAssessmentValidationError(
            f"'symptoms' must contain at most {_MAX_SYMPTOM_TEXT_LENGTH} characters."
        )
    if not any(character.isalnum() for character in text):
        raise SymptomAssessmentValidationError(
            "'symptoms' must contain letters or numbers."
        )
    return text


def _validate_optional_integer(
    value: object,
    field_name: str,
    maximum: int,
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise SymptomAssessmentValidationError(
            f"'{field_name}' must be an integer."
        )
    if value < 0 or value > maximum:
        raise SymptomAssessmentValidationError(
            f"'{field_name}' must be between 0 and {maximum}."
        )
    return value


def _normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_accents = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )
    return " ".join(re.sub(r"[^a-z0-9]+", " ", without_accents).split())


def _find_matches(
    normalized_text: str,
    definitions: tuple[tuple[str, tuple[str, ...]], ...],
) -> list[str]:
    padded_text = f" {normalized_text} "
    return [
        identifier
        for identifier, phrases in definitions
        if any(f" {phrase} " in padded_text for phrase in phrases)
    ]


def _determine_severity(
    *,
    recognized: list[str],
    red_flags: list[str],
    category: object,
    age: int | None,
    duration_days: int | None,
) -> tuple[str, list[str]]:
    if red_flags:
        return "urgent", ["urgent_red_flag_detected"]
    if age == 0 and "fever" in recognized:
        return "urgent", ["infant_age_with_fever"]

    moderate_reasons: list[str] = []
    if not recognized:
        moderate_reasons.append("no_controlled_symptom_match")
    elif category is None:
        moderate_reasons.append("no_supported_category_match")
    if age is not None and (age <= 5 or age >= 65):
        moderate_reasons.append("age_requires_additional_caution")
    if duration_days is not None and duration_days >= 7:
        moderate_reasons.append("symptoms_present_for_seven_or_more_days")
    if "fever" in recognized and duration_days is not None:
        if duration_days >= 3:
            moderate_reasons.append("fever_present_for_three_or_more_days")

    if moderate_reasons:
        return "moderate", moderate_reasons
    return "mild", ["short_duration_without_detected_red_flags"]


def _recommended_action(severity: str) -> str:
    if severity == "urgent":
        return (
            "Seek urgent medical care now. Do not use this result to select "
            "or purchase medication."
        )
    if severity == "moderate":
        return (
            "Consult a qualified healthcare professional or pharmacist before "
            "choosing a medication; seek urgent care if symptoms worsen."
        )
    return (
        "Monitor symptoms and ask a qualified pharmacist for general information; "
        "seek medical care if symptoms worsen or persist."
    )
