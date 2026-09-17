"""Catálogo controlado y reglas educativas para síntomas.

Define datos inmutables usados por el clasificador interno; no procesa entradas ni
consulta servicios externos. Las categorías son simuladas y acotadas, por lo que nunca
deben interpretarse como diagnóstico o cobertura médica completa."""

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
