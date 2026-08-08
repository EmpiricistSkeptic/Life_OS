# life/services/domains/nutrition.py
from __future__ import annotations
from datetime import date
from typing import Dict, Any, Optional, List

from django.utils import timezone

from life.models import Domain
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


# ── metric name constants ─────────────────────────────────────────────────────

NUTRITION_METRIC_NAMES = {
    "calories":  {"Calories", "Total Calories", "Калории", "Калорийность"},
    "protein":   {"Protein", "Белки", "Белок"},
    "fat":       {"Fat", "Жиры", "Жир"},
    "carbs":     {"Carbs", "Carbohydrates", "Углеводы"},
    "water":     {"Water", "Вода"},
}

# ккал на грамм
KCAL_PER_G = {"protein": 4.0, "fat": 9.0, "carbs": 4.0}

# дефолтный целевой баланс макросов (fallback если нет целей)
DEFAULT_MACRO_TARGET = {"protein": 0.30, "fat": 0.30, "carbs": 0.40}


def _safe_mean(lst: List[float]) -> float:
    return float(sum(lst) / len(lst)) if lst else 0.0


def _ensure_date(d: Optional[date]) -> date:
    return d or timezone.localdate()


def _classify_metric(name: str) -> Optional[str]:
    """Определяет тип метрики по имени."""
    name_lower = name.lower()
    for key, aliases in NUTRITION_METRIC_NAMES.items():
        for alias in aliases:
            if alias.lower() in name_lower or name_lower in alias.lower():
                return key
    return None


def _daily_values(series: List[Dict]) -> List[float]:
    """Извлекает ненулевые значения из daily_series."""
    return [float(d["value"]) for d in series if (d.get("value") or 0) > 0]


def _pct_days_on_target(series: List[Dict], goal_target: float, tolerance: float = 0.10) -> Optional[float]:
    """
    Процент дней когда значение было в пределах goal_target ± tolerance.
    Работает для калорий с целью at_most: дни ≤ goal_target × (1 + tolerance).
    """
    values = _daily_values(series)
    if not values:
        return None
    lo = goal_target * (1 - tolerance)
    hi = goal_target * (1 + tolerance)
    on_target = sum(1 for v in values if lo <= v <= hi)
    return round(on_target / len(values) * 100, 1)


def _compute_macro_balance_score(
    actual: Dict[str, float],   # {"protein": avg_g, "fat": avg_g, "carbs": avg_g}
    target: Dict[str, float],   # {"protein": pct, "fat": pct, "carbs": pct}
) -> float:
    """
    Считает macro_balance_score 0-100.
    Чем ближе реальный баланс к целевому — тем выше.
    """
    macro_kcal = {
        k: actual.get(k, 0.0) * KCAL_PER_G[k]
        for k in ("protein", "fat", "carbs")
    }
    total_kcal = sum(macro_kcal.values())
    if total_kcal <= 0:
        return 0.0

    actual_pct = {k: macro_kcal[k] / total_kcal for k in macro_kcal}

    deviation = sum(
        abs(actual_pct.get(k, 0.0) - target.get(k, DEFAULT_MACRO_TARGET[k]))
        for k in ("protein", "fat", "carbs")
    ) / 3.0

    # 10% среднее отклонение = -30 очков, 33% = 0
    score = max(0.0, 100.0 - deviation * 300.0)
    return round(score, 1)


def _macro_target_from_goals(
    goal_map: Dict[str, Any],   # {"protein": goal_info, "fat": ..., "carbs": ...}
    calorie_goal: Optional[float],
) -> Dict[str, float]:
    """
    Вычисляет целевой баланс макросов из UserGoal.
    Возвращает {"protein": pct, "fat": pct, "carbs": pct}.
    Fallback на DEFAULT_MACRO_TARGET если данных недостаточно.
    """
    targets_g: Dict[str, float] = {}
    for macro in ("protein", "fat", "carbs"):
        g = goal_map.get(macro)
        if g and g.get("target"):
            targets_g[macro] = float(g["target"])

    if len(targets_g) < 3:
        return DEFAULT_MACRO_TARGET.copy()

    # Переводим г → ккал
    targets_kcal = {k: targets_g[k] * KCAL_PER_G[k] for k in targets_g}
    total = sum(targets_kcal.values())
    if total <= 0:
        return DEFAULT_MACRO_TARGET.copy()

    return {k: round(targets_kcal[k] / total, 4) for k in targets_kcal}


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
    macro_avgs: Dict[str, float] = {}   # {"protein": avg_g, ...}
    macro_goals: Dict[str, Any] = {}    # {"protein": goal_info, ...}
    calorie_avg: Optional[float] = None
    calorie_goal_target: Optional[float] = None
    calorie_series: List[Dict] = []
    water_avg: Optional[float] = None
    water_goal_target: Optional[float] = None

    for metric in metrics:
        metric_type = _classify_metric(metric.name)
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
        active_days = sum(1 for d in series_window if (d.get("value") or 0) > 0)

        try:
            gr = growth_rate(metric, start_window, end)
        except Exception:
            gr = None

        goal_info = compute_goal_progress_for_metric(metric, user, end)
        is_inverted = (
            goal_info.get("comparison") == "at_most"
            if goal_info else False
        )

        # ── intensity_score ──────────────────────────────────────────────────
        intensity_value = window_avg if agg_type == "avg" else window_sum

        if goal_info and goal_info.get("target") and goal_info["target"] > 0:
            if agg_type == "avg":
                max_ref = float(goal_info["target"])
            else:
                goal_period = goal_info.get("period", "weekly")
                max_ref = _goal_target_as_period_equivalent(
                    goal_info["target"], goal_period, window_days
                )
        else:
            if agg_type == "avg":
                max_ref = monthly_avg or max(window_avg, 1.0)
            else:
                daily_baseline = monthly_sum / baseline_days if monthly_sum > 0 else 0.0
                expected = daily_baseline * window_days
                max_ref = expected if expected > 0 else max(window_sum, 1.0)

        if is_inverted:
            if intensity_value <= max_ref:
                intensity_score = 100.0
            else:
                overshoot = (intensity_value - max_ref) / max_ref if max_ref > 0 else 1.0
                intensity_score = max(0.0, 100.0 - overshoot * 100.0)
        else:
            intensity_score = normalize_to_0_100(intensity_value, 0.0, max_ref)

        # ── stability_score ──────────────────────────────────────────────────
        mean_daily = monthly_sum / baseline_days if monthly_sum > 0 else 0.0
        if active_days < MIN_ACTIVE_FOR_STABILITY:
            stability_score = None
        elif mean_daily <= 0:
            stability_score = 100.0 if std == 0 else 50.0
        else:
            rel = std / mean_daily
            stability_score = max(0.0, min(100.0, (1.0 - rel) * 100.0))

        # ── growth_score ─────────────────────────────────────────────────────
        if gr is None:
            growth_score = None
        else:
            if is_inverted:
                growth_score = max(0.0, 50.0 - gr * 50.0) if gr >= 0 else min(100.0, 50.0 + abs(gr) * 50.0)
            else:
                growth_score = min(100.0, 50.0 + gr * 50.0) if gr >= 0 else max(0.0, 50.0 + gr * 50.0)

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
            "window_sum":    round(window_sum, 3),
            "window_avg":    round(window_avg, 3),
            "monthly_sum":   round(monthly_sum, 3),
            "monthly_avg":   round(monthly_avg, 3),
            "std":           round(std, 6),
            "consistency":   round(consistency, 6),
            "growth":        None if gr is None else round(gr, 6),
            "goal":          _sanitize_goal_info(goal_info),
            "metric_score":  round(metric_score, 3),
            "intensity_score": round(intensity_score, 3),
            "stability_score": None if stability_score is None else round(stability_score, 3),
            "growth_score":    None if growth_score is None else round(growth_score, 3),
        }

        intensity_scores.append(intensity_score)
        consistency_scores.append(consistency * 100.0)
        if stability_score is not None:
            stability_scores.append(stability_score)
        if growth_score is not None:
            growth_scores.append(growth_score)
        if goal_align is not None:
            goal_align_scores.append(goal_align)

        # ── собираем данные для specific_summary ─────────────────────────────
        if metric_type == "calories":
            calorie_avg = window_avg
            calorie_series = series_window
            if goal_info and goal_info.get("target"):
                calorie_goal_target = float(goal_info["target"])
        elif metric_type in ("protein", "fat", "carbs"):
            macro_avgs[metric_type] = window_avg
            if goal_info and goal_info.get("target"):
                macro_goals[metric_type] = goal_info
        elif metric_type == "water":
            water_avg = window_avg
            if goal_info and goal_info.get("target"):
                water_goal_target = float(goal_info["target"])

    # ── domain summary ────────────────────────────────────────────────────────
    BASE_WEIGHTS = {
        "intensity": 0.35, "consistency": 0.25,
        "stability": 0.20, "growth": 0.10, "goal": 0.10,
    }
    components: Dict[str, float] = {
        "intensity":   _safe_mean(intensity_scores),
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
        "intensity":    round(_safe_mean(intensity_scores), 3),
        "consistency":  round(_safe_mean(consistency_scores), 3),
        "stability":    round(_safe_mean(stability_scores), 3) if stability_scores else None,
        "growth":       round(_safe_mean(growth_scores), 3) if growth_scores else None,
        "goal_alignment": round(_safe_mean(goal_align_scores), 3) if goal_align_scores else None,
        "domain_score": domain_score,
    }

    # ── specific_summary ──────────────────────────────────────────────────────

    # макро-баланс
    macro_target_pct = _macro_target_from_goals(macro_goals, calorie_goal_target)

    macro_kcal = {
        k: macro_avgs.get(k, 0.0) * KCAL_PER_G[k]
        for k in ("protein", "fat", "carbs")
    }
    macro_kcal_total = sum(macro_kcal.values())

    macro_protein_pct = round(macro_kcal["protein"] / macro_kcal_total * 100, 1) if macro_kcal_total > 0 else None
    macro_fat_pct     = round(macro_kcal["fat"]     / macro_kcal_total * 100, 1) if macro_kcal_total > 0 else None
    macro_carbs_pct   = round(macro_kcal["carbs"]   / macro_kcal_total * 100, 1) if macro_kcal_total > 0 else None

    macro_balance_score = (
        _compute_macro_balance_score(macro_avgs, macro_target_pct)
        if len(macro_avgs) == 3 else None
    )

    # calorie_goal_delta — среднее отклонение от цели (+ = профицит, - = дефицит)
    calorie_goal_delta = None
    if calorie_avg is not None and calorie_goal_target is not None:
        calorie_goal_delta = round(calorie_avg - calorie_goal_target, 1)

    # pct_days_on_target по калориям
    pct_days_on_target = None
    if calorie_series and calorie_goal_target:
        pct_days_on_target = _pct_days_on_target(calorie_series, calorie_goal_target)

    # calorie_variability — std калорий по дням
    calorie_variability = None
    if calorie_avg is not None:
        cal_values = _daily_values(calorie_series)
        if len(cal_values) >= 2:
            mean = _safe_mean(cal_values)
            variance = sum((v - mean) ** 2 for v in cal_values) / len(cal_values)
            calorie_variability = round(variance ** 0.5, 1)

    # protein_per_100kcal
    protein_per_100kcal = None
    if calorie_avg and calorie_avg > 0 and macro_avgs.get("protein"):
        protein_per_100kcal = round(macro_avgs["protein"] / calorie_avg * 100, 1)

    # water
    water_goal_pct = None
    if water_avg is not None and water_goal_target and water_goal_target > 0:
        water_goal_pct = round(min(water_avg / water_goal_target * 100, 100), 1)

    # рекомендации
    recommendations: List[str] = []

    if calorie_goal_delta is not None:
        if calorie_goal_delta > 200:
            recommendations.append(f"Средний профицит {calorie_goal_delta:.0f} ккал/день — если цель похудение, сократи порции.")
        elif calorie_goal_delta < -200:
            recommendations.append(f"Средний дефицит {abs(calorie_goal_delta):.0f} ккал/день — следи чтобы не было слишком большого дефицита.")

    if macro_balance_score is not None and macro_balance_score < 60:
        # определяем какой макрос сильнее всего отклонился
        deviations = {
            k: abs((macro_kcal.get(k, 0) / macro_kcal_total if macro_kcal_total > 0 else 0) - macro_target_pct.get(k, 0))
            for k in ("protein", "fat", "carbs")
        }
        worst_macro = max(deviations, key=lambda k: deviations[k])
        labels = {"protein": "белков", "fat": "жиров", "carbs": "углеводов"}
        recommendations.append(f"Баланс макросов {macro_balance_score:.0f}/100 — больше всего отклонение по {labels[worst_macro]}.")

    if protein_per_100kcal is not None and protein_per_100kcal < 5:
        recommendations.append("Мало белка на 100 ккал — рассмотри более белковые продукты.")

    if water_goal_pct is not None and water_goal_pct < 80:
        recommendations.append(f"Выполнение цели по воде {water_goal_pct:.0f}% — старайся пить больше.")

    if pct_days_on_target is not None and pct_days_on_target < 50:
        recommendations.append("Менее половины дней калорийность в рамках цели — питание нестабильное.")

    if not recommendations:
        recommendations.append("Питание в норме — продолжай в том же ритме.")

    specific_summary: Dict[str, Any] = {
        # калории
        "avg_daily_calories":   round(calorie_avg, 1) if calorie_avg is not None else None,
        "calorie_goal_delta":   calorie_goal_delta,
        "pct_days_on_target":   pct_days_on_target,
        "calorie_variability":  calorie_variability,
        # макросы — средние граммы
        "avg_protein_g":  round(macro_avgs["protein"], 1) if "protein" in macro_avgs else None,
        "avg_fat_g":      round(macro_avgs["fat"], 1)     if "fat"     in macro_avgs else None,
        "avg_carbs_g":    round(macro_avgs["carbs"], 1)   if "carbs"   in macro_avgs else None,
        # макро-баланс
        "macro_protein_pct":   macro_protein_pct,
        "macro_fat_pct":       macro_fat_pct,
        "macro_carbs_pct":     macro_carbs_pct,
        "macro_balance_score": macro_balance_score,
        # целевой баланс (для справки на фронте)
        "target_protein_pct":  round(macro_target_pct["protein"] * 100, 1),
        "target_fat_pct":      round(macro_target_pct["fat"]     * 100, 1),
        "target_carbs_pct":    round(macro_target_pct["carbs"]   * 100, 1),
        # белковая плотность
        "protein_per_100kcal": protein_per_100kcal,
        # вода
        "avg_water_ml":    round(water_avg, 0) if water_avg is not None else None,
        "water_goal_pct":  water_goal_pct,
        "recommendations": recommendations,
    }

    return {
        "domain_id":        domain.pk,
        "domain_name":      domain.name,
        "slug":             getattr(domain, "slug", None),
        "period":           {"type": period, "start": start_window.isoformat(), "end": end.isoformat()},
        "report":           {"per_metric": per_metric, "summary": summary},
        "specific_summary": specific_summary,
    }