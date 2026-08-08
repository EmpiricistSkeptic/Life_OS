# life/services/templates/languages.py

"""
Шаблон метрик для slug = "languages".
Каждый элемент - dict с минимальным набором:
- name (человеко-читаемое имя метрики)
- key (машинное имя/slug для метрики, опционально)
- unit (единица измерения)
- aggregation (aggregation_type для модели Metric, например "sum" или "avg")
"""

LANGUAGE_METRICS = [
    {"key": "listening_minutes", "name": "Listening Minutes", "unit": "minutes", "aggregation": "sum"},
    {"key": "speaking_minutes",  "name": "Speaking Minutes",  "unit": "minutes", "aggregation": "sum"},
    {"key": "reading_minutes",   "name": "Reading Minutes",   "unit": "minutes", "aggregation": "sum"},
    {"key": "writing_minutes",   "name": "Writing Minutes",   "unit": "minutes", "aggregation": "sum"},
    {"key": "vocab_new",        "name": "Vocabulary New",     "unit": "count",   "aggregation": "sum"},
    {"key": "vocab_review",     "name": "Vocabulary Review",  "unit": "count",   "aggregation": "sum"},
    {"key": "grammar_ex",       "name": "Grammar Exercises",  "unit": "count",   "aggregation": "sum"},
]