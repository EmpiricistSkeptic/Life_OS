# life/services/core/stats.py
from __future__ import annotations
from datetime import date, timedelta
from typing import Dict, Optional, List, Tuple

import statistics

from django.utils import timezone

from life.models import Metric, UserGoal
from .aggregation import (
    date_range_for_days,
    aggregate_metric_range,
    daily_series,
    daily_series_for_last_n,
    weekly_aggregate,
    monthly_aggregate,
    summary_for_period,
    growth_rate,
)


# -------------------------
# Утилиты / простые функции
# -------------------------

def _ensure_date(d: Optional[date]) -> date:
    if d is None:
        return timezone.localdate()
    return d


def _period_to_days(period: str) -> int:
    """
    Конвертирует строку period в количество дней для основного окна расчётов.
    weekly  → 7
    monthly → 30
    days:NN → NN
    """
    if period == "weekly":
        return 7
    if period == "monthly":
        return 30
    if isinstance(period, str) and period.startswith("days:"):
        try:
            return int(period.split(":", 1)[1])
        except (ValueError, IndexError):
            pass
    return 7  # fallback


def std_dev_for_period(metric: Metric, start_date: date, end_date: date) -> float:
    series = daily_series(metric, start_date, end_date)
    values = [item["value"] for item in series]
    if not values:
        return 0.0
    try:
        return float(statistics.pstdev(values))
    except statistics.StatisticsError:
        return 0.0


def consistency_index(metric: Metric, start_date: date, end_date: date) -> float:
    series = daily_series(metric, start_date, end_date)
    total_days = len(series)
    if total_days == 0:
        return 0.0
    active_days = sum(1 for d in series if d["value"] != 0.0)
    return active_days / total_days


def normalize_to_0_100(value: float, min_ref: float, max_ref: float, invert: bool = False) -> float:
    try:
        min_r = float(min_ref)
        max_r = float(max_ref)
    except Exception:
        return 0.0

    if max_r == min_r:
        return 100.0 if value >= max_r else 0.0

    clamped = max(min(value, max_r), min_r)
    ratio = (clamped - min_r) / (max_r - min_r)
    if invert:
        ratio = 1.0 - ratio
    return float(max(0.0, min(100.0, ratio * 100.0)))


# -------------------------
# Goal logic
# -------------------------

def _goal_target_as_period_equivalent(target: float, goal_period: str, window_days: int) -> float:
    """
    Конвертирует целевое значение цели в эквивалент для текущего окна расчёта.

    Например, если цель daily=7.5 часов сна, а окно = 30 дней →
    эталон для сравнения с суммой за 30 дней = 7.5 * 30 = 225.

    goal_period: период цели ('daily'|'weekly'|'monthly')
    window_days: размер текущего окна расчёта в днях
    """
    # Сначала конвертируем target в дневной эквивалент
    if goal_period == "daily":
        daily_target = target
    elif goal_period == "weekly":
        daily_target = target / 7.0
    elif goal_period == "monthly":
        daily_target = target / 30.0
    else:
        daily_target = target / 7.0

    # Затем масштабируем на размер окна
    return daily_target * window_days


def compute_goal_progress_for_metric(metric: Metric, user, end_date: Optional[date] = None) -> Optional[Dict]:
    """
    Возвращает прогресс по активной цели для данной метрики.

    Учитывает metric.aggregation_type:
    - aggregation_type='avg' → сравниваем по среднему (Sleep Duration, Stress Level и т.п.)
    - aggregation_type='sum' → сравниваем по сумме
    """
    goal = UserGoal.objects.filter(
        user=user, metric=metric, is_active=True
    ).order_by("-updated_at").first()
    if not goal:
        return None

    end = _ensure_date(end_date)
    period = goal.period

    agg_type = getattr(metric, "aggregation_type", "sum")
    agg_key = "avg" if agg_type == "avg" else "sum"

    if period == "daily":
        agg = aggregate_metric_range(metric, end, end)
    elif period == "weekly":
        agg = weekly_aggregate(metric, end)
    elif period == "monthly":
        agg = monthly_aggregate(metric, end, days=30)
    else:
        agg = weekly_aggregate(metric, end)

    current = float(agg.get(agg_key, 0.0) or 0.0)
    target = float(goal.target_value or 0.0)
    cmp_type = goal.comparison_type

    progress = 0.0
    if cmp_type == "at_least":
        if target <= 0:
            progress = 1.0 if current > 0 else 0.0
        else:
            progress = min(current / target, 1.0)
    # стало:
    elif cmp_type == "at_most":
        if agg.get("count", 0) == 0:
            # Нет записей за период — не считаем цель выполненной
            progress = 0.0
        elif target <= 0:
            progress = 1.0 if current <= 0 else 0.0
        else:
            if current <= target:
                progress = 1.0
            else:
                progress = max(0.0, 1.0 - ((current - target) / target))
    elif cmp_type == "exact":
        if target <= 0:
            progress = 1.0 if current == target else 0.0
        else:
            progress = max(0.0, 1.0 - (abs(current - target) / target))

    progress = max(0.0, min(1.0, float(progress)))

    return {
        "goal": goal,
        "progress": progress,
        "current_value": float(current),
        "target": target,
        "comparison": cmp_type,
        "period": period,
    }


# -------------------------
# Domain-level basic scores
# -------------------------

def domain_basic_scores(domain, user, end_date: Optional[date] = None, period: str = "weekly") -> Dict:
    """
    Расчёт метрик домена с динамическим окном на основе period.

    period определяет:
    - основное окно расчёта (window_days): intensity, growth, std
    - окно consistency: max(window_days, 30) — всегда не меньше 30 дней
      для стабильности показателя
    - окно для monthly агрегата (baseline): всегда 30 дней независимо от period,
      так как это исторический baseline

    Изменения относительно предыдущей версии:
    ──────────────────────────────────────────
    - period пробрасывается сюда из вьюсета и используется для динамических окон
    - intensity_score: max_ref = _goal_target_as_period_equivalent(target, goal_period, window_days)
      корректно масштабирует эталон под любой размер окна
    - consistency считается за max(window_days, 30) дней
    - stability guard: MIN_ACTIVE_DAYS = max(3, window_days // 10)
    - growth сравнивает текущее окно с предыдущим окном того же размера
    - веса нормируются пропорционально при отсутствии отдельных компонент
    """
    end = _ensure_date(end_date)
    window_days = _period_to_days(period)

    # Consistency считаем за не менее 30 дней для стабильности
    consistency_days = max(window_days, 30)
    # Baseline всегда за 30 дней — это исторический ориентир
    baseline_days = 30
    # Минимум активных дней для расчёта stability
    MIN_ACTIVE_FOR_STABILITY = max(3, window_days // 10)

    per_metric = {}
    intensity_scores: List[float] = []
    consistency_scores: List[float] = []
    stability_scores: List[float] = []
    growth_scores: List[float] = []
    goal_align_scores: List[float] = []
    metric_scores: List[float] = []

    metrics = list(domain.metrics.all())

    for metric in metrics:
        # --- агрегаты за основное окно ---
        start_window, _ = date_range_for_days(end, window_days)
        window_agg = aggregate_metric_range(metric, start_window, end)
        window_sum = float(window_agg.get("sum", 0.0) or 0.0)
        window_avg = float(window_agg.get("avg", 0.0) or 0.0)
        agg_type = getattr(metric, "aggregation_type", "sum")

        # --- baseline за 30 дней (исторический ориентир) ---
        monthly = monthly_aggregate(metric, end, days=baseline_days)
        monthly_sum = float(monthly.get("sum", 0.0) or 0.0)
        monthly_avg_entry = float(monthly.get("avg", 0.0) or 0.0)

        # --- consistency за consistency_days ---
        start_consistency, _ = date_range_for_days(end, consistency_days)
        consistency = consistency_index(metric, start_consistency, end)

        # --- std за основное окно ---
        std = std_dev_for_period(metric, start_window, end)

        # --- активных дней за основное окно для stability guard ---
        series_window = daily_series(metric, start_window, end)
        active_days_window = sum(1 for d in series_window if d["value"] != 0.0)

        # --- growth: текущее окно vs предыдущее окно того же размера ---
        try:
            gr = growth_rate(metric, start_window, end)
        except Exception:
            gr = None

        # --- goal ---
        goal_info = compute_goal_progress_for_metric(metric, user, end)

        # стало:
        if agg_type == "avg":
            intensity_value = window_avg
            is_inverted = goal_info.get("comparison") == "at_most" if goal_info else False
            if goal_info and goal_info["target"] > 0:
                max_ref = goal_info["target"]
            else:
                max_ref = float(monthly.get("avg", 0.0) or 0.0) or max(window_avg, 1.0)
        else:
            # Для sum-метрик всё как раньше — суммируем и масштабируем через цель
            intensity_value = window_sum
            is_inverted = False
            if goal_info and goal_info["target"] > 0:
                goal_period = goal_info.get("period", "weekly")
                max_ref = _goal_target_as_period_equivalent(
                    goal_info["target"], goal_period, window_days
                )
            else:
                daily_baseline = monthly_sum / baseline_days if monthly_sum > 0 else 0.0
                expected = daily_baseline * window_days
                max_ref = expected if expected > 0 else max(window_sum, 1.0)

        if is_inverted:
            # at_most: значение <= target → 100%, превышение → пропорциональный штраф
            if intensity_value <= max_ref:
                intensity_score = 100.0
            else:
                overshoot = (intensity_value - max_ref) / max_ref if max_ref > 0 else 1.0
                intensity_score = max(0.0, 100.0 - overshoot * 100.0)
        else:
            intensity_score = normalize_to_0_100(intensity_value, 0.0, max_ref, invert=False)

        # ─── stability_score ─────────────────────────────────────────────────
        mean_daily = monthly_sum / baseline_days if monthly_sum > 0 else 0.0

        if active_days_window < MIN_ACTIVE_FOR_STABILITY:
            stability_score = None  # недостаточно данных
        elif mean_daily <= 0:
            stability_score = 100.0 if std == 0 else 50.0
        else:
            rel = std / mean_daily
            stability_score = max(0.0, min(100.0, (1.0 - rel) * 100.0))

        # ─── growth_score ─────────────────────────────────────────────────
        if gr is None:
            growth_score = None  # нет истории — не участвует в усреднении
        elif gr >= 0:
            growth_score = min(100.0, 50.0 + gr * 50.0)
        else:
            growth_score = max(0.0, 50.0 + gr * 50.0)

        # --- goal alignment ---
        goal_progress = goal_info["progress"] if goal_info else None
        goal_align = (goal_progress * 100.0) if goal_progress is not None else None

        # --- metric_score ---
        stability_for_score = stability_score if stability_score is not None else 50.0
        growth_for_score = growth_score if growth_score is not None else 50.0

        metric_score = (
            intensity_score * 0.40
            + consistency * 100.0 * 0.25
            + stability_for_score * 0.20
            + growth_for_score * 0.15
        )

        per_metric[metric.name] = {
            "window_sum": window_sum,       # сумма за активное окно (period)
            "window_avg": window_avg,
            "monthly_sum": monthly_sum,
            "monthly_avg": monthly_avg_entry,
            "std": std,
            "consistency": consistency,
            "growth": gr,
            "goal": goal_info,
            "metric_score": metric_score,
            "intensity_score": intensity_score,
            "stability_score": stability_score,
            "growth_score": growth_score,
        }

        metric_scores.append(metric_score)
        intensity_scores.append(intensity_score)
        consistency_scores.append(consistency * 100.0)

        if stability_score is not None:
            stability_scores.append(stability_score)
        if growth_score is not None:
            growth_scores.append(growth_score)
        if goal_align is not None:
            goal_align_scores.append(goal_align)

    # --- summary ---
    def avg_safe(lst: List[float]) -> Optional[float]:
        return float(sum(lst) / len(lst)) if lst else None

    intensity_avg = avg_safe(intensity_scores) or 0.0
    consistency_avg = avg_safe(consistency_scores) or 0.0
    stability_avg = avg_safe(stability_scores)
    growth_avg = avg_safe(growth_scores)
    goal_avg = avg_safe(goal_align_scores)

    summary = {
        "intensity": intensity_avg,
        "consistency": consistency_avg,
        "stability": stability_avg,
        "growth": growth_avg,
        "goal_alignment": goal_avg,
    }

    # ─── нормировка весов ────────────────────────────────────────────────────
    BASE_WEIGHTS = {
        "intensity":   0.35,
        "consistency": 0.25,
        "stability":   0.20,
        "growth":      0.10,
        "goal":        0.10,
    }

    components: Dict[str, float] = {
        "intensity": intensity_avg,
        "consistency": consistency_avg,
    }
    if stability_avg is not None:
        components["stability"] = stability_avg
    if growth_avg is not None:
        components["growth"] = growth_avg
    if goal_avg is not None:
        components["goal"] = goal_avg

    active_weight_sum = sum(BASE_WEIGHTS[k] for k in components)
    domain_score = 0.0
    if active_weight_sum > 0:
        for key, val in components.items():
            normalized_w = BASE_WEIGHTS[key] / active_weight_sum
            domain_score += val * normalized_w

    domain_score = max(0.0, min(100.0, domain_score))
    summary["domain_score"] = domain_score

    return {"per_metric": per_metric, "summary": summary}