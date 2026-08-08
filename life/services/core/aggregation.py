# life/services/core/aggregation.py
from __future__ import annotations
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from django.db.models import Avg, Max, Min, Sum, Count
from django.db.models.functions import TruncDate
from django.utils import timezone

from life.models import MetricEntry, Metric


def _ensure_date(d: Optional[date]) -> date:
    """Если передано None — вернуть today (по timezone)."""
    if d is None:
        return timezone.localdate()
    return d


def date_range_for_days(end_date: Optional[date], days: int) -> Tuple[date, date]:
    """
    Возвращает (start, end) включительно для последних `days` дней,
    где end = end_date, start = end - (days - 1)
    """
    end = _ensure_date(end_date)
    start = end - timedelta(days=days - 1)
    return start, end


def _metric_entries_qs(metric: Metric, start_date: date, end_date: date):
    """
    Возвращает queryset MetricEntry, отфильтрованный по metric и даты (включительно).
    Сравнение по created_at__date (удобно для дневных аггрегаций).
    """
    return MetricEntry.objects.filter(
        metric=metric,
        created_at__date__gte=start_date,
        created_at__date__lte=end_date
    )


def aggregate_entries(qs) -> Dict[str, Optional[float]]:
    """
    Принимает queryset MetricEntry и возвращает агрегаты.
    Возвращаемые ключи: sum, avg, min, max, count.
    Если нет записей — значения будут None (или 0 для count).
    """
    agg = qs.aggregate(
        total=Sum("value"),
        avg=Avg("value"),
        min_val=Min("value"),
        max_val=Max("value"),
        count=Count("pk"),
    )
    return {
        "sum": float(agg["total"]) if agg["total"] is not None else 0.0,
        "avg": float(agg["avg"]) if agg["avg"] is not None else 0.0,
        "min": float(agg["min_val"]) if agg["min_val"] is not None else None,
        "max": float(agg["max_val"]) if agg["max_val"] is not None else None,
        "count": int(agg["count"]) if agg["count"] is not None else 0,
    }


def aggregate_metric_range(metric: Metric, start_date: date, end_date: date) -> Dict[str, Optional[float]]:
    """
    Агрегирует значения metric за период [start_date, end_date].
    Возвращает тот же словарь, что и aggregate_entries.
    """
    qs = _metric_entries_qs(metric, start_date, end_date)
    return aggregate_entries(qs)


def weekly_aggregate(metric: Metric, end_date: Optional[date] = None) -> Dict[str, Optional[float]]:
    """Aggregate for the last 7 days (including end_date)."""
    start, end = date_range_for_days(end_date, days=7)
    return aggregate_metric_range(metric, start, end)


def monthly_aggregate(metric: Metric, end_date: Optional[date] = None, days: int = 30) -> Dict[str, Optional[float]]:
    """Aggregate for the last `days` days (default 30)."""
    start, end = date_range_for_days(end_date, days=days)
    return aggregate_metric_range(metric, start, end)


def daily_series(metric: Metric, start_date: date, end_date: date) -> List[Dict]:
    """
    Возвращает список суточных сумм по датам в периоде [start_date, end_date].
    Формат: [{ "date": ISO_date_str, "value": float }, ...] для каждой даты в диапазоне.
    Суммы по дням берутся через annotate(TruncDate) и Sum.
    Если на какой-то день нет записей — value = 0.0.
    """
    qs = _metric_entries_qs(metric, start_date, end_date)
    # агрегируем по дате
    annotated = (
        qs
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .order_by("day")
        .annotate(day_sum=Sum("value"))
        .values("day", "day_sum")
    )

    # build a dict day -> value
    sums_by_day = {item["day"]: float(item["day_sum"] or 0.0) for item in annotated}

    # full sequence from start_date to end_date
    days = []
    cur = start_date
    while cur <= end_date:
        days.append({"date": cur.isoformat(), "value": float(sums_by_day.get(cur, 0.0))})
        cur = cur + timedelta(days=1)
    return days


def daily_series_for_last_n(metric: Metric, end_date: Optional[date], n_days: int) -> List[Dict]:
    """Удобный wrapper для последних n_days."""
    start, end = date_range_for_days(end_date, days=n_days)
    return daily_series(metric, start, end)


def active_days_count(metric: Metric, start_date: date, end_date: date) -> int:
    """Число дней в периоде с ненулевыми суммами (days with activity)."""
    series = daily_series(metric, start_date, end_date)
    return sum(1 for d in series if d["value"] != 0.0)


def growth_rate(metric: Metric, start_date: date, end_date: date) -> Optional[float]:
    """
    Сравнивает суммарное значение за [start_date, end_date] с предшествующим периодом
    такого же размера. Возвращает относительную разницу (current - previous) / previous.
    Если previous == 0 -> возвращает None (или можно вернуть большой рост).
    """
    current_agg = aggregate_metric_range(metric, start_date, end_date)
    current_sum = current_agg.get("sum", 0.0)

    period_days = (end_date - start_date).days + 1
    prev_end = start_date - timedelta(days=1)
    prev_start = prev_end - timedelta(days=period_days - 1)

    prev_agg = aggregate_metric_range(metric, prev_start, prev_end)
    prev_sum = prev_agg.get("sum", 0.0)

    if prev_sum == 0:
        return None
    return (current_sum - prev_sum) / prev_sum


# ---- Example convenience helpers combining above ----

def summary_for_period(metric: Metric, start_date: date, end_date: date) -> Dict:
    """
    Convenience: возвращает полный summary для периода:
    - агрегаты (sum, avg, min, max, count)
    - daily_series
    - active_days
    """
    agg = aggregate_metric_range(metric, start_date, end_date)
    series = daily_series(metric, start_date, end_date)
    active = sum(1 for item in series if item["value"] != 0.0)
    return {
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "aggregates": agg,
        "daily_series": series,
        "active_days": active,
    }