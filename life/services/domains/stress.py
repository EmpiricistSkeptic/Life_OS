# life/services/domains/stress.py
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

STRESS_METRIC_NAMES = [
    "Stress Level",
    "Meditation Minutes",
    "Exercise Minutes",
    "Relaxation Minutes",
]

# Метрики где меньше = лучше
INVERTED_METRICS = {"Stress Level"}

# Sum метрики (recovery активности)
RECOVERY_METRICS = {"Meditation Minutes", "Exercise Minutes", "Relaxation Minutes"}


def _safe_mean(lst: List[float]) -> float:
    return float(sum(lst) / len(lst)) if lst else 0.0


def _ensure_date(d: Optional[date]) -> date:
    return d or timezone.localdate()


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

    per_metric: Dict[str, Dict[str, Any]] = {}
    intensity_scores: List[float] = []
    consistency_scores: List[float] = []
    stability_scores: List[float] = []
    growth_scores: List[float] = []
    goal_align_scores: List[float] = []

    # для specific_summary
    recovery_totals: Dict[str, float] = {}

    def get_metric(name: str) -> Optional[Metric]:
        try:
            return domain.metrics.get(name=name)
        except Metric.DoesNotExist:
            return None

    for name in STRESS_METRIC_NAMES:
        metric = get_metric(name)
        if not metric:
            per_metric[name] = {
                "window_sum": 0.0, "window_avg": 0.0,
                "monthly_sum": 0.0, "monthly_avg": 0.0,
                "std": 0.0, "consistency": 0.0, "growth": None, "goal": None,
                "metric_score": 0.0, "intensity_score": 0.0,
                "stability_score": None, "growth_score": None,
            }
            if name in RECOVERY_METRICS:
                recovery_totals[name] = 0.0
            continue

        agg_type = getattr(metric, "aggregation_type", "avg")

        window_agg = aggregate_metric_range(metric, start_window, end)
        window_sum = float(window_agg.get("sum", 0.0) or 0.0)
        window_avg = float(window_agg.get("avg", 0.0) or 0.0)

        monthly_agg = aggregate_metric_range(metric, start_month, end)
        monthly_sum = float(monthly_agg.get("sum", 0.0) or 0.0)
        monthly_avg = float(monthly_agg.get("avg", 0.0) or 0.0)

        consistency = float(consistency_index(metric, start_consistency, end) or 0.0)
        std = float(std_dev_for_period(metric, start_window, end) or 0.0)

        series_window = daily_series(metric, start_window, end)
        active_days_window = sum(1 for d in series_window if d["value"] != 0.0)

        if name in RECOVERY_METRICS:
            recovery_totals[name] = window_sum

        try:
            gr = growth_rate(metric, start_window, end)
        except Exception:
            gr = None

        goal_info = compute_goal_progress_for_metric(metric, user, end)

        # ── intensity_score ──────────────────────────────────────────────────
        is_inverted = name in INVERTED_METRICS

        if agg_type == "avg":
            intensity_value = window_avg
            if goal_info and goal_info["target"] > 0:
                max_ref = goal_info["target"]
                is_inverted = goal_info.get("comparison") == "at_most"
            else:
                max_ref = monthly_avg or max(window_avg, 1.0)
                if name in INVERTED_METRICS:
                    is_inverted = True

            if is_inverted:
                if intensity_value <= max_ref:
                    intensity_score = 100.0
                else:
                    overshoot = (intensity_value - max_ref) / max_ref if max_ref > 0 else 1.0
                    intensity_score = max(0.0, 100.0 - overshoot * 100.0)
            else:
                intensity_score = normalize_to_0_100(intensity_value, 0.0, max_ref)
        else:
            # sum метрики (recovery активности)
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
        # для стресса рост = плохо, поэтому инвертируем growth_score
        if gr is None:
            growth_score = None
        elif name in INVERTED_METRICS:
            # стресс растёт → плохо
            if gr <= 0:
                growth_score = min(100.0, 50.0 + abs(gr) * 50.0)
            else:
                growth_score = max(0.0, 50.0 - gr * 50.0)
        else:
            if gr >= 0:
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

        per_metric[name] = {
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
        }

        intensity_scores.append(intensity_score)
        consistency_scores.append(consistency * 100.0)
        if stability_score is not None:
            stability_scores.append(stability_score)
        if growth_score is not None:
            growth_scores.append(growth_score)
        if goal_align is not None:
            goal_align_scores.append(goal_align)

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
    stress_metric = get_metric("Stress Level")

    avg_stress = 0.0
    stress_trend = None
    if stress_metric:
        stress_agg = aggregate_metric_range(stress_metric, start_window, end)
        avg_stress = round(float(stress_agg.get("avg", 0.0) or 0.0), 2)
        try:
            stress_trend = round(float(growth_rate(stress_metric, start_window, end)), 4)
        except Exception:
            stress_trend = None

    meditation = recovery_totals.get("Meditation Minutes", 0.0)
    exercise = recovery_totals.get("Exercise Minutes", 0.0)
    relaxation = recovery_totals.get("Relaxation Minutes", 0.0)
    total_recovery = meditation + exercise + relaxation

    # recovery_score: нормируем к 0–100 относительно baseline
    # цель: ~300 мин recovery в неделю = 100%
    RECOVERY_TARGET_WEEKLY = 300.0
    recovery_score = round(min(100.0, (total_recovery / RECOVERY_TARGET_WEEKLY) * 100.0), 1)

    # stress-recovery баланс: минут recovery на 1 балл стресса
    recovery_per_stress = round(total_recovery / avg_stress, 1) if avg_stress > 0 else None

    # stress_label
    def _stress_label(v: float) -> str:
        if v <= 1.5: return "Очень спокойно"
        if v <= 2.5: return "Небольшой стресс"
        if v <= 3.5: return "Умеренный стресс"
        if v <= 4.5: return "Высокий стресс"
        return "Очень высокий стресс"

    # рекомендации
    recommendations: List[str] = []
    stress_goal_info = compute_goal_progress_for_metric(stress_metric, user, end) if stress_metric else None
    target_stress = stress_goal_info["target"] if stress_goal_info and stress_goal_info["target"] > 0 else 3.0

    if avg_stress > target_stress:
        recommendations.append(
            f"Уровень стресса {avg_stress} выше цели {target_stress} — увеличь recovery активности."
        )
    if total_recovery < 60:
        recommendations.append(
            "Очень мало recovery активностей за период — добавь хотя бы 20 мин медитации или прогулок в день."
        )
    if meditation < 30:
        recommendations.append(
            "Мало медитации — даже 10 мин в день значительно снижают стресс."
        )
    if exercise < 60:
        recommendations.append(
            "Физическая активность ниже нормы — старайся двигаться хотя бы 20 мин в день."
        )
    if stress_trend is not None and stress_trend > 0.1:
        recommendations.append(
            "Стресс растёт — уделяй больше внимания восстановлению и сну."
        )
    if recovery_per_stress is not None and recovery_per_stress < 30:
        recommendations.append(
            "Низкий баланс recovery/stress — на каждый балл стресса должно приходиться минимум 30 мин восстановления."
        )
    if not recommendations:
        recommendations.append(
            "Отличный баланс стресса и восстановления — поддерживай текущий режим!"
        )

    specific_summary: Dict[str, Any] = {
        "avg_stress_level": avg_stress,
        "stress_label": _stress_label(avg_stress),
        "stress_trend": stress_trend,
        "total_meditation_minutes": round(meditation, 1),
        "total_exercise_minutes": round(exercise, 1),
        "total_relaxation_minutes": round(relaxation, 1),
        "total_recovery_minutes": round(total_recovery, 1),
        "recovery_score": recovery_score,
        "recovery_per_stress_point": recovery_per_stress,
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