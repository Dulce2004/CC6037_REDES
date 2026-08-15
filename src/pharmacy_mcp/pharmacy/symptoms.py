"""Catálogo controlado y reglas educativas para síntomas."""

RECOGNIZED_SYMPTOMS = frozenset(
    {
        "fever",
        "cough",
        "sore_throat",
        "nasal_congestion",
        "sneezing",
        "itchy_eyes",
        "nausea",
        "diarrhea",
        "abdominal_pain",
    }
)

CATEGORY_SYMPTOMS = {
    "respiratory": frozenset({"fever", "cough", "sore_throat"}),
    "allergy": frozenset({"sneezing", "nasal_congestion", "itchy_eyes"}),
    "gastrointestinal": frozenset({"nausea", "diarrhea", "abdominal_pain"}),
}

MINIMUM_MATCHING_SYMPTOMS = 2
