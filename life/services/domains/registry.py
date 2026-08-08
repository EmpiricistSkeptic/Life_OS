# life/services/domains/registry.py
from __future__ import annotations
from typing import Callable, Optional, Dict
import importlib

from . import generic

HandlerFn = Callable[..., dict]

# Явный список известных ключей (опционально)
KNOWN_KEYS = (
    "default", "generic", "sleep", "language", "nutrition",
    "training", "programming", "habits"
)


def _load_domain_module(key: str):
    """
    Пытается импортировать life.services.domains.<key>.
    Возвращает модуль или None.
    """
    module_path = f"life.services.domains.{key}"
    try:
        module = importlib.import_module(module_path)
        return module
    except ModuleNotFoundError:
        return None
    except Exception:
        # на случай других ошибок — не ломаем систему, вернём None
        return None


def get_service(key: Optional[str]) -> HandlerFn:
    """
    Возвращает функцию get_domain_report для handler key.
    Если нет ключа или нет модуля — возвращает generic.get_domain_report.
    key может быть slug из модели Domain или явно заданный handler.
    """
    if not key:
        return generic.get_domain_report

    # normalize key
    key_norm = str(key).strip().lower()

    # try to load domain-specific module dynamically
    module = _load_domain_module(key_norm)
    if module and hasattr(module, "get_domain_report"):
        return getattr(module, "get_domain_report")

    # fallback: try generic
    return generic.get_domain_report


def available_handlers() -> Dict[str, str]:
    """
    Возвращает словарь известных handlers -> module_path (или 'generic' если отсутствует).
    Удобно для админки/дебага.
    """
    out = {}
    for k in KNOWN_KEYS:
        module = _load_domain_module(k)
        out[k] = module.__name__ if module is not None else "generic"
    return out