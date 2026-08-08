# life/services/templates/__init__.py

from .languages import LANGUAGE_METRICS
from .sleep import SLEEP_METRICS
from .stress import STRESS_METRICS
from .habit import HABIT_METRICS
from .nutrition import NUTRITION_METRICS

TEMPLATES = {
    "language": LANGUAGE_METRICS,
    "sleep": SLEEP_METRICS,
    "stress": STRESS_METRICS,
    "habit": HABIT_METRICS,
    "nutrition": NUTRITION_METRICS,
}


def get_template_by_slug(slug: str):
    """
    Возвращает список метрик (template) для slug или None если нет.
    """
    return TEMPLATES.get(slug)