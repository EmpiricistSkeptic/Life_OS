# life/services/domains/sleep.py
from __future__ import annotations
import math
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

# Имена метрик — должны совпадать с тем что создаёт шаблон
SLEEP_METRIC_NAMES = [
    "Sleep Duration",
    "Sleep Quality",
    "Bedtime",
    "Wake Time",
    "Awakenings",
    "Sleep Latency",
    "Nap Duration",
]

# Метрики с временем в минутах — требуют circular statistics
CIRCULAR_METRICS = {"Bedtime", "Wake Time"}

# Метрики где "меньше = лучше" — intensity инвертируется
INVERTED_METRICS = {"Awakenings", "Sleep Latency"}


# ── circular statistics helpers ───────────────────────────────────────────────

def _circular_mean_minutes(values: List[float]) -> Optional[float]:
    """
    Среднее время суток по кругу (минуты 0..1439).
    Обычное среднее некорректно для значений около полуночи:
    среднее между 23:50 (1430) и 00:10 (10) должно быть 00:00 (0/1440),
    а не 720 (12:00). Circular mean решает эту проблему.
    """
    if not values:
        return None
    radians = [v / 1440.0 * 2 * math.pi for v in values]
    x = sum(math.cos(r) for r in radians) / len(radians)
    y = sum(math.sin(r) for r in radians) / len(radians)
    mean_angle = math.atan2(y, x)
    mean_minutes = (mean_angle / (2 * math.pi)) * 1440
    if mean_minutes < 0:
        mean_minutes += 1440
    return mean_minutes


def _circular_std_minutes(values: List[float]) -> Optional[float]:
    """
    Circular std для времени в минутах.
    R — длина среднего вектора (0..1): R=1 → все значения одинаковы, R=0 → максимальный разброс.
    """
    if not values:
        return None
    radians = [v / 1440.0 * 2 * math.pi for v in values]
    x = sum(math.cos(r) for r in radians) / len(radians)
    y = sum(math.sin(r) for r in radians) / len(radians)
    R = math.sqrt(x ** 2 + y ** 2)
    if R < 1e-9:
        return 720.0  # максимальная вариативность
    circ_std = math.sqrt(-2 * math.log(R)) * (1440 / (2 * math.pi))
    return round(circ_std, 2)


def _minutes_to_hhmm(minutes: float) -> str:
    """1380.0 → '23:00', 30.0 → '00:30'"""
    h = int(minutes // 60) % 24
    m = int(minutes % 60)
    return f"{h:02d}:{m:02d}"


def _safe_mean(lst: List[float]) -> float:
    return float(sum(lst) / len(lst)) if lst else 0.0


def _ensure_date(d: Optional[date]) -> date:
    return d or timezone.localdate()


# ── main ──────────────────────────────────────────────────────────────────────

def get_domain_report(domain: Domain, user, end_date: Optional[date] = None, period: str = "weekly") -> Dict[str, Any]:
    """
    Специфический отчёт для домена Sleep.
    Возвращает структуру совместимую с generic.get_domain_report + specific_summary.
    """
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

    # для circular stats собираем raw values по имени метрики
    circular_raw: Dict[str, List[float]] = {name: [] for name in CIRCULAR_METRICS}

    def get_metric(name: str) -> Optional[Metric]:
        try:
            return domain.metrics.get(name=name)
        except Metric.DoesNotExist:
            return None

    for name in SLEEP_METRIC_NAMES:
        metric = get_metric(name)
        if not metric:
            per_metric[name] = {
                "window_sum": 0.0, "window_avg": 0.0,
                "monthly_sum": 0.0, "monthly_avg": 0.0,
                "std": 0.0, "consistency": 0.0, "growth": None, "goal": None,
                "metric_score": 0.0, "intensity_score": 0.0,
                "stability_score": None, "growth_score": None,
            }
            continue

        agg_type = getattr(metric, "aggregation_type", "avg")

        # агрегаты за окно
        window_agg = aggregate_metric_range(metric, start_window, end)
        window_sum = float(window_agg.get("sum", 0.0) or 0.0)
        window_avg = float(window_agg.get("avg", 0.0) or 0.0)

        # baseline за 30 дней
        monthly_agg = aggregate_metric_range(metric, start_month, end)
        monthly_sum = float(monthly_agg.get("sum", 0.0) or 0.0)
        monthly_avg = float(monthly_agg.get("avg", 0.0) or 0.0)

        # consistency за max(window, 30) дней
        consistency = float(consistency_index(metric, start_consistency, end) or 0.0)

        # std за основное окно
        std = float(std_dev_for_period(metric, start_window, end) or 0.0)

        # активных дней в окне для stability guard
        series_window = daily_series(metric, start_window, end)
        active_days_window = sum(1 for d in series_window if d["value"] != 0.0)

        # для circular метрик собираем raw значения
        if name in CIRCULAR_METRICS:
            circular_raw[name] = [d["value"] for d in series_window if d["value"] != 0.0]

        # growth
        try:
            gr = growth_rate(metric, start_window, end)
        except Exception:
            gr = None

        # goal
        goal_info = compute_goal_progress_for_metric(metric, user, end)

        # ── intensity_score ──────────────────────────────────────────────────
        is_inverted = name in INVERTED_METRICS
        # Для circular метрик (Bedtime, Wake Time) intensity не очень осмысленна,
        # ставим нейтральный 50 если нет цели
        if name in CIRCULAR_METRICS:
            intensity_score = 50.0
        elif agg_type == "avg":
            intensity_value = window_avg
            if goal_info and goal_info["target"] > 0:
                max_ref = goal_info["target"]
                is_inverted = goal_info.get("comparison") == "at_most"
            else:
                max_ref = monthly_avg or max(window_avg, 1.0)
                # для INVERTED_METRICS без цели — инвертируем относительно baseline
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
            # sum метрики (Nap Duration)
            intensity_value = window_sum
            is_inverted = False
            if goal_info and goal_info["target"] > 0:
                goal_period = goal_info.get("period", "weekly")
                max_ref = _goal_target_as_period_equivalent(goal_info["target"], goal_period, window_days)
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

        # goal alignment
        goal_progress = goal_info["progress"] if goal_info else None
        goal_align = (goal_progress * 100.0) if goal_progress is not None else None

        # metric_score
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

    # ── domain summary (взвешенная нормировка как везде) ─────────────────────
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
    sleep_dur_metric = get_metric("Sleep Duration")
    sleep_qual_metric = get_metric("Sleep Quality")
    awakenings_metric = get_metric("Awakenings")

    # avg sleep hours за окно
    avg_sleep_hours = 0.0
    sleep_variability = 0.0
    pct_nights_meeting_target = None
    sleep_debt = None

    if sleep_dur_metric:
        dur_series = daily_series(sleep_dur_metric, start_window, end)
        dur_values = [d["value"] for d in dur_series if d["value"] != 0.0]
        avg_sleep_hours = round(_safe_mean(dur_values), 2)
        sleep_variability = round(float(std_dev_for_period(sleep_dur_metric, start_window, end)), 2)

        # pct ночей с выполненной целью и sleep debt
        dur_goal = compute_goal_progress_for_metric(sleep_dur_metric, user, end)
        if dur_goal and dur_goal["target"] > 0:
            target_h = dur_goal["target"]
            nights_ok = sum(1 for v in dur_values if v >= target_h)
            pct_nights_meeting_target = round(nights_ok / len(dur_values) * 100.0, 1) if dur_values else 0.0
            actual_sum = sum(dur_values)
            expected_sum = target_h * window_days
            sleep_debt = round(max(0.0, expected_sum - actual_sum), 2)

    # avg sleep quality
    avg_sleep_quality = None
    if sleep_qual_metric:
        qual_agg = aggregate_metric_range(sleep_qual_metric, start_window, end)
        avg_sleep_quality = round(float(qual_agg.get("avg", 0.0) or 0.0), 2)

    # avg awakenings
    avg_awakenings = None
    if awakenings_metric:
        awk_agg = aggregate_metric_range(awakenings_metric, start_window, end)
        avg_awakenings = round(float(awk_agg.get("avg", 0.0) or 0.0), 2)

    # circular stats для Bedtime и Wake Time
    bedtime_vals = circular_raw.get("Bedtime", [])
    wake_vals = circular_raw.get("Wake Time", [])

    bedtime_mean_min = _circular_mean_minutes(bedtime_vals)
    bedtime_std_min = _circular_std_minutes(bedtime_vals)
    wake_mean_min = _circular_mean_minutes(wake_vals)
    wake_std_min = _circular_std_minutes(wake_vals)

    # midpoint variability: если есть оба — считаем midpoint per day
    midpoint_std = None
    if bedtime_vals and wake_vals and len(bedtime_vals) == len(wake_vals):
        midpoints = []
        for b, w in zip(bedtime_vals, wake_vals):
            # midpoint = (bedtime + duration/2) по кругу
            # упрощённо: среднее между bedtime и wake_time по кругу
            diff = (w - b) % 1440
            mid = (b + diff / 2) % 1440
            midpoints.append(mid)
        midpoint_std = _circular_std_minutes(midpoints)

    # рекомендации
    recommendations: List[str] = []
    dur_goal_info = compute_goal_progress_for_metric(sleep_dur_metric, user, end) if sleep_dur_metric else None
    target_hours = dur_goal_info["target"] if dur_goal_info and dur_goal_info["target"] > 0 else 8.0

    if avg_sleep_hours > 0 and avg_sleep_hours < target_hours:
        recommendations.append(f"Увеличь время сна — цель {target_hours}ч, сейчас в среднем {avg_sleep_hours}ч.")
    if sleep_variability > 1.5:
        recommendations.append("Высокая нестабильность длительности сна — старайся ложиться в одно время.")
    if bedtime_std_min and bedtime_std_min > 60:
        recommendations.append("Вариативность времени засыпания > 60 мин — уменьши экранное время перед сном.")
    if avg_sleep_quality is not None and avg_sleep_quality < 3.0:
        recommendations.append("Низкое качество сна — проверь гигиену сна и условия в спальне.")
    if avg_awakenings is not None and avg_awakenings > 2:
        recommendations.append("Частые пробуждения — возможно стоит проконсультироваться со специалистом.")
    if not recommendations:
        recommendations.append("Отличная динамика сна — поддерживай режим!")

    specific_summary: Dict[str, Any] = {
        "avg_sleep_hours_week": avg_sleep_hours,
        "avg_sleep_quality_week": avg_sleep_quality,
        "avg_awakenings": avg_awakenings,
        "pct_nights_meeting_target": pct_nights_meeting_target,
        "sleep_variability_hours": sleep_variability,
        "sleep_debt_estimate": sleep_debt,
        "bedtime_mean": _minutes_to_hhmm(bedtime_mean_min) if bedtime_mean_min is not None else None,
        "bedtime_std_minutes": int(bedtime_std_min) if bedtime_std_min is not None else None,
        "wake_time_mean": _minutes_to_hhmm(wake_mean_min) if wake_mean_min is not None else None,
        "wake_time_std_minutes": int(wake_std_min) if wake_std_min is not None else None,
        "sleep_midpoint_variability_minutes": round(midpoint_std, 1) if midpoint_std is not None else None,
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