# life/services/domains/generic.py
from __future__ import annotations
from datetime import date
from typing import Dict, Any, Optional, Tuple

from django.utils import timezone

from life.models import Domain, Metric  # модели проекта
from life.services.core.stats import domain_basic_scores
from life.services.core.aggregation import date_range_for_days


def _ensure_date(d: Optional[date]) -> date:
    return d or timezone.localdate()


def _sanitize_goal_info(goal_info: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Преобразует структуру goal_info (которая может содержать Django-объект Goal)
    в JSON-сериализуемый словарь с нужными полями.
    """
    if not goal_info:
        return None

    goal = goal_info.get("goal")
    return {
        "goal_id": getattr(goal, "id", None),
        "progress": float(goal_info.get("progress", 0.0)),
        "current_value": float(goal_info.get("current_value", 0.0)),
        "target": float(goal_info.get("target", 0.0)),
        "comparison": goal_info.get("comparison"),
        "period": goal_info.get("period"),
    }


def _sanitize_per_metric(per_metric: Dict[str, Dict]) -> Dict[str, Dict]:
    """
    Проходит по per_metric (как вернул domain_basic_scores) и убирает
    несериализуемые элементы (например, объект Goal) заменяя их простыми
    словарями.
    """
    out: Dict[str, Dict] = {}
    for metric_name, metric_data in per_metric.items():
        # shallow copy to avoid mutating original
        md = dict(metric_data)
        # sanitize goal
        if "goal" in md and md["goal"] is not None:
            md["goal"] = _sanitize_goal_info(md["goal"])
        out[metric_name] = md
    return out


def _get_period_dates(end_date: Optional[date], period: str) -> Tuple[date, date]:
    """
    Возвращает (start, end) для запрошенного period.
    Поддерживаем 'weekly' и 'monthly' и кастомные 'n_days' форматом 'days:NN'.
    """
    end = _ensure_date(end_date)
    if period == "weekly":
        return date_range_for_days(end, 7)
    if period == "monthly":
        return date_range_for_days(end, 30)
    # поддержать формат days:NN, например 'days:14'
    if isinstance(period, str) and period.startswith("days:"):
        try:
            n = int(period.split(":", 1)[1])
            return date_range_for_days(end, n)
        except Exception:
            pass
    # fallback: weekly
    return date_range_for_days(end, 7)


def get_domain_report(domain: Domain, user, end_date: Optional[date] = None, period: str = "weekly") -> Dict[str, Any]:
    """
    Универсальная функция для получения отчёта по домену.

    Args:
        domain: instance of Domain
        user: request.user (для поиска Goal и персональных данных)
        end_date: optional end date (date). If None - today.
        period: 'weekly'|'monthly'|'days:NN' - controls returned period meta (does not change core stats,
                which are computed for last 7/30 days inside domain_basic_scores by default).
                We still provide period start/end for the response.

    Returns: JSON-ready dict:
      {
        "domain_id": ...,
        "domain_name": "...",
        "slug": "...",
        "period": {"type": period, "start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
        "report": {
            "per_metric": {... sanitized ...},
            "summary": {...}
        }
      }
    """
    # Determine period dates for metadata
    start, end = _get_period_dates(end_date, period)

    # call the heavy-lifting function (domain_basic_scores)
    # domain_basic_scores itself uses end_date to compute last-7-days stats
    report = domain_basic_scores(domain, user, end_date=end, period=period)

    # sanitize per_metric to remove Django model instances
    per_metric = report.get("per_metric", {})
    safe_per_metric = _sanitize_per_metric(per_metric)

    summary = report.get("summary", {})

    result = {
        "domain_id": domain.id,
        "domain_name": domain.name,
        "slug": getattr(domain, "slug", None),
        "period": {
            "type": period,
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        "report": {
            "per_metric": safe_per_metric,
            "summary": summary,
        },
    }

    return result