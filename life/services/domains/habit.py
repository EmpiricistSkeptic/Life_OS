# life/services/domains/habit.py
from __future__ import annotations
from datetime import date
from typing import Dict, Any, Optional, List

from django.utils import timezone

from life.models import Domain, Metric
from life.services.core.aggregation import (
    date_range_for_days,
    aggregate_metric_range,
    daily_series,
    growth_rate,
)
from life.services.core.stats import (
    std_dev_for_period,
    consistency_index,
    compute_goal_progress_for_metric,
    normalize_to_0_100,
    _period_to_days,
    _goal_target_as_period_equivalent,
)
from life.services.domains.generic import _sanitize_goal_info


def _safe_mean(lst: List[float]) -> float:
    return float(sum(lst) / len(lst)) if lst else 0.0


def _ensure_date(d: Optional[date]) -> date:
    return d or timezone.localdate()


def _compute_streak(series: List[Dict]) -> Dict[str, Any]:
    """
    Считает streak метрики из daily_series.
    series = [{"date": ..., "value": float}, ...]
    value > 0 считается как выполненный день.
    Серия прерывается при value == 0 или отсутствии записи.
    """
    values = [1 if (d.get("value") or 0) > 0 else 0 for d in series]

    if not values:
        return {
            "current_streak": 0,
            "longest_streak": 0,
            "avg_streak": 0.0,
            "fail_count": 0,
        }

    # current_streak — считаем с конца
    current_streak = 0
    for v in reversed(values):
        if v == 1:
            current_streak += 1
        else:
            break

    # longest_streak, avg_streak, fail_count
    streaks = []
    current = 0
    fail_count = 0
    for v in values:
        if v == 1:
            current += 1
        else:
            if current > 0:
                streaks.append(current)
                current = 0
            fail_count += 1
    if current > 0:
        streaks.append(current)

    longest_streak = max(streaks) if streaks else 0
    avg_streak = round(_safe_mean(streaks), 1) if streaks else 0.0

    return {
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "avg_streak": avg_streak,
        "fail_count": fail_count,
    }


def get_domain_report(
    domain: Domain,
    user,
    end_date: Optional[date] = None,
    period: str = "weekly",
) -> Dict[str, Any]:
    end = _ensure_date(end_date)
    window_days = _period_to_days(period)
    consistency_days = max(window_days, 30)
    baseline_days = 30
    MIN_ACTIVE_FOR_STABILITY = max(3, window_days // 10)

    start_window, _ = date_range_for_days(end, window_days)
    start_consistency, _ = date_range_for_days(end, consistency_days)
    start_month, _ = date_range_for_days(end, baseline_days)

    metrics = list(domain.metrics.all())

    per_metric: Dict[str, Dict[str, Any]] = {}
    intensity_scores: List[float] = []
    consistency_scores: List[float] = []
    stability_scores: List[float] = []
    growth_scores: List[float] = []
    goal_align_scores: List[float] = []

    # для specific_summary
    habit_summaries: List[Dict[str, Any]] = []

    for metric in metrics:
        agg_type = getattr(metric, "aggregation_type", "sum")

        window_agg = aggregate_metric_range(metric, start_window, end)
        window_sum = float(window_agg.get("sum", 0.0) or 0.0)
        window_avg = float(window_agg.get("avg", 0.0) or 0.0)

        monthly_agg = aggregate_metric_range(metric, start_month, end)
        monthly_sum = float(monthly_agg.get("sum", 0.0) or 0.0)
        monthly_avg = float(monthly_agg.get("avg", 0.0) or 0.0)

        consistency = float(consistency_index(metric, start_consistency, end) or 0.0)
        std = float(std_dev_for_period(metric, start_window, end) or 0.0)

        series_window = daily_series(metric, start_window, end)
        active_days_window = sum(1 for d in series_window if (d.get("value") or 0) > 0)

        # streak данные за 30 дней для более значимых серий
        series_month = daily_series(metric, start_month, end)
        streak_data = _compute_streak(series_month)

        # completion_rate за окно
        completion_rate = round(active_days_window / window_days, 4) if window_days > 0 else 0.0

        try:
            gr = growth_rate(metric, start_window, end)
        except Exception:
            gr = None

        goal_info = compute_goal_progress_for_metric(metric, user, end)

        # ── intensity_score ──────────────────────────────────────────────────
        if agg_type == "avg":
            intensity_value = window_avg
            if goal_info and goal_info["target"] > 0:
                max_ref = goal_info["target"]
                is_inverted = goal_info.get("comparison") == "at_most"
            else:
                max_ref = monthly_avg or max(window_avg, 1.0)
                is_inverted = False
            if is_inverted:
                if intensity_value <= max_ref:
                    intensity_score = 100.0
                else:
                    overshoot = (intensity_value - max_ref) / max_ref if max_ref > 0 else 1.0
                    intensity_score = max(0.0, 100.0 - overshoot * 100.0)
            else:
                intensity_score = normalize_to_0_100(intensity_value, 0.0, max_ref)
        else:
            intensity_value = window_sum
            if goal_info and goal_info["target"] > 0:
                goal_period = goal_info.get("period", "weekly")
                max_ref = _goal_target_as_period_equivalent(
                    goal_info["target"], goal_period, window_days
                )
            else:
                daily_baseline = monthly_sum / baseline_days if monthly_sum > 0 else 0.0
                expected = daily_baseline * window_days
                max_ref = expected if expected > 0 else max(window_sum, 1.0)
            intensity_score = normalize_to_0_100(intensity_value, 0.0, max_ref)

        # ── stability_score ──────────────────────────────────────────────────
        mean_daily = monthly_sum / baseline_days if monthly_sum > 0 else 0.0
        if active_days_window < MIN_ACTIVE_FOR_STABILITY:
            stability_score = None
        elif mean_daily <= 0:
            stability_score = 100.0 if std == 0 else 50.0
        else:
            rel = std / mean_daily
            stability_score = max(0.0, min(100.0, (1.0 - rel) * 100.0))

        # ── growth_score ─────────────────────────────────────────────────────
        if gr is None:
            growth_score = None
        elif gr >= 0:
            growth_score = min(100.0, 50.0 + gr * 50.0)
        else:
            growth_score = max(0.0, 50.0 + gr * 50.0)

        goal_progress = goal_info["progress"] if goal_info else None
        goal_align = (goal_progress * 100.0) if goal_progress is not None else None

        stability_for_score = stability_score if stability_score is not None else 50.0
        growth_for_score = growth_score if growth_score is not None else 50.0
        metric_score = (
            intensity_score * 0.40
            + consistency * 100.0 * 0.25
            + stability_for_score * 0.20
            + growth_for_score * 0.15
        )

        per_metric[metric.name] = {
            "window_sum": round(window_sum, 3),
            "window_avg": round(window_avg, 3),
            "monthly_sum": round(monthly_sum, 3),
            "monthly_avg": round(monthly_avg, 3),
            "std": round(std, 6),
            "consistency": round(consistency, 6),
            "growth": None if gr is None else round(gr, 6),
            "goal": _sanitize_goal_info(goal_info),
            "metric_score": round(metric_score, 3),
            "intensity_score": round(intensity_score, 3),
            "stability_score": None if stability_score is None else round(stability_score, 3),
            "growth_score": None if growth_score is None else round(growth_score, 3),
            # habit-specific поля прямо в per_metric
            "completion_rate": completion_rate,
            "current_streak": streak_data["current_streak"],
            "longest_streak": streak_data["longest_streak"],
            "avg_streak": streak_data["avg_streak"],
            "fail_count": streak_data["fail_count"],
        }

        intensity_scores.append(intensity_score)
        consistency_scores.append(consistency * 100.0)
        if stability_score is not None:
            stability_scores.append(stability_score)
        if growth_score is not None:
            growth_scores.append(growth_score)
        if goal_align is not None:
            goal_align_scores.append(goal_align)

        habit_summaries.append({
            "name": metric.name,
            "completion_rate": completion_rate,
            "current_streak": streak_data["current_streak"],
            "longest_streak": streak_data["longest_streak"],
            "on_track": goal_align is not None and goal_align >= 80.0,
            "metric_score": round(metric_score, 3),
        })

    # ── domain summary ────────────────────────────────────────────────────────
    BASE_WEIGHTS = {
        "intensity": 0.35, "consistency": 0.25,
        "stability": 0.20, "growth": 0.10, "goal": 0.10,
    }
    components: Dict[str, float] = {
        "intensity": _safe_mean(intensity_scores),
        "consistency": _safe_mean(consistency_scores),
    }
    if stability_scores:
        components["stability"] = _safe_mean(stability_scores)
    if growth_scores:
        components["growth"] = _safe_mean(growth_scores)
    if goal_align_scores:
        components["goal"] = _safe_mean(goal_align_scores)

    active_weight_sum = sum(BASE_WEIGHTS[k] for k in components)
    domain_score = 0.0
    if active_weight_sum > 0:
        for k, val in components.items():
            domain_score += val * BASE_WEIGHTS[k] / active_weight_sum
    domain_score = round(max(0.0, min(100.0, domain_score)), 3)

    summary = {
        "intensity": round(_safe_mean(intensity_scores), 3),
        "consistency": round(_safe_mean(consistency_scores), 3),
        "stability": round(_safe_mean(stability_scores), 3) if stability_scores else None,
        "growth": round(_safe_mean(growth_scores), 3) if growth_scores else None,
        "goal_alignment": round(_safe_mean(goal_align_scores), 3) if goal_align_scores else None,
        "domain_score": domain_score,
    }

    # ── specific_summary ──────────────────────────────────────────────────────
    habits_on_track = sum(1 for h in habit_summaries if h["on_track"])
    total_habits = len(habit_summaries)

    best_habit = max(habit_summaries, key=lambda h: h["completion_rate"]) if habit_summaries else None
    worst_habit = min(habit_summaries, key=lambda h: h["completion_rate"]) if habit_summaries else None
    longest_streak_habit = max(habit_summaries, key=lambda h: h["current_streak"]) if habit_summaries else None

    avg_completion_rate = round(_safe_mean([h["completion_rate"] for h in habit_summaries]), 4)

    # рекомендации
    recommendations: List[str] = []
    if avg_completion_rate < 0.5:
        recommendations.append("Менее половины привычек выполняется — попробуй сократить список до 2-3 ключевых.")
    if worst_habit and worst_habit["completion_rate"] < 0.3:
        recommendations.append(f"Привычка «{worst_habit['name']}» выполняется менее 30% дней — пересмотри цель или частоту.")
    if longest_streak_habit and longest_streak_habit["current_streak"] >= 7:
        recommendations.append(f"Отличная серия {longest_streak_habit['current_streak']} дней в «{longest_streak_habit['name']}» — поддерживай!")
    if habits_on_track == total_habits and total_habits > 0:
        recommendations.append("Все привычки выполняются в рамках цели — отличная неделя!")
    if not recommendations:
        recommendations.append("Хороший прогресс — продолжай в том же темпе.")

    specific_summary: Dict[str, Any] = {
        "total_habits": total_habits,
        "habits_on_track": habits_on_track,
        "avg_completion_rate": avg_completion_rate,
        "best_habit": best_habit["name"] if best_habit else None,
        "best_habit_rate": best_habit["completion_rate"] if best_habit else None,
        "worst_habit": worst_habit["name"] if worst_habit else None,
        "worst_habit_rate": worst_habit["completion_rate"] if worst_habit else None,
        "longest_active_streak_habit": longest_streak_habit["name"] if longest_streak_habit else None,
        "longest_active_streak": longest_streak_habit["current_streak"] if longest_streak_habit else 0,
        "habits": habit_summaries,  # детали по каждой привычке
        "recommendations": recommendations,
    }

    return {
        "domain_id": domain.pk,
        "domain_name": domain.name,
        "slug": getattr(domain, "slug", None),
        "period": {"type": period, "start": start_window.isoformat(), "end": end.isoformat()},
        "report": {"per_metric": per_metric, "summary": summary},
        "specific_summary": specific_summary,
    }