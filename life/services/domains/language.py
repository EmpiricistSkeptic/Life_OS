from __future__ import annotations
from typing import Dict, Any, Optional, List
from datetime import date
import statistics

from django.utils import timezone

from life.models import Domain, Metric
from life.services.core.aggregation import (
    date_range_for_days,
    aggregate_metric_range,
    daily_series_for_last_n,
    daily_series,
    growth_rate,
)
from life.services.core.stats import (
    std_dev_for_period,
    consistency_index,
    compute_goal_progress_for_metric,
    _period_to_days,
)
from life.services.domains.generic import _sanitize_goal_info

LANGUAGE_METRIC_NAMES = [
    "Listening Minutes",
    "Speaking Minutes",
    "Reading Minutes",
    "Writing Minutes",
    "Vocabulary New",
    "Vocabulary Review",
    "Grammar Exercises",
]


def _ensure_date(d: Optional[date]) -> date:
    return d or timezone.localdate()


def _safe_mean(lst: List[float]) -> float:
    return float(sum(lst) / len(lst)) if lst else 0.0


def get_domain_report(domain: Domain, user, end_date: Optional[date] = None, period: str = "weekly") -> Dict[str, Any]:
    end = _ensure_date(end_date)
    window_days = _period_to_days(period)  # ИСПРАВЛЕНИЕ 3: динамическое окно

    start_window, end_window = date_range_for_days(end, window_days)
    start_month, end_month = date_range_for_days(end, 30)

    def get_metric(name: str) -> Optional[Metric]:
        try:
            return domain.metrics.get(name=name)
        except Metric.DoesNotExist:
            return None

    per_metric: Dict[str, Dict[str, Any]] = {}
    intensity_scores: List[float] = []
    consistency_scores: List[float] = []
    stability_scores: List[float] = []
    growth_scores: List[float] = []
    totals = {}

    for name in LANGUAGE_METRIC_NAMES:
        metric = get_metric(name)
        if not metric:
            per_metric[name] = {
                "window_sum": 0.0,
                "monthly_sum": 0.0,
                "monthly_avg": 0.0,
                "std": 0.0,
                "consistency": 0.0,
                "growth": None,
                "goal": None,
                "metric_score": 0.0,
                "intensity_score": 0.0,
                "stability_score": None,
                "growth_score": None,
            }
            totals[name] = 0.0
            continue

        window_agg = aggregate_metric_range(metric, start_window, end_window)
        weekly_sum = float(window_agg.get("sum", 0.0) or 0.0)
        monthly_agg = aggregate_metric_range(metric, start_month, end_month)
        monthly_sum = float(monthly_agg.get("sum", 0.0) or 0.0)
        monthly_avg = float(monthly_agg.get("avg", 0.0) or 0.0)

        std = float(std_dev_for_period(metric, start_window, end_window) or 0.0)
        consistency = float(consistency_index(metric, start_month, end_month) or 0.0)

        try:
            gr = growth_rate(metric, start_window, end_window)
        except Exception:
            gr = None

        goal_info = compute_goal_progress_for_metric(metric, user=user, end_date=end)

        # ИСПРАВЛЕНИЕ 5: intensity с поддержкой at_most
        baseline_daily = (monthly_sum / 30.0) if monthly_sum > 0 else 0.0
        expected = baseline_daily * window_days if baseline_daily > 0 else max(weekly_sum, 1.0)

        is_inverted = goal_info.get("comparison") == "at_most" if goal_info else False
        if is_inverted:
            if weekly_sum <= expected:
                intensity_score = 100.0
            else:
                overshoot = (weekly_sum - expected) / expected if expected > 0 else 1.0
                intensity_score = max(0.0, 100.0 - overshoot * 100.0)
        else:
            intensity_score = min(100.0, (weekly_sum / expected) * 100.0) if expected > 0 else 0.0

        mean_daily = baseline_daily
        if mean_daily <= 0:
            stability_score = 100.0 if std == 0 else 50.0
        else:
            rel = std / (mean_daily + 1e-9)
            stability_score = max(0.0, min(100.0, (1.0 - rel) * 100.0))

        if gr is None:
            growth_score = None
        elif gr >= 0:
            growth_score = min(100.0, 50.0 + gr * 50.0)
        else:
            growth_score = max(0.0, 50.0 + gr * 50.0)

        stability_for_score = stability_score if stability_score is not None else 50.0
        growth_for_score = growth_score if growth_score is not None else 50.0
        metric_score = (
            intensity_score * 0.45
            + (consistency * 100.0) * 0.20
            + stability_for_score * 0.20
            + growth_for_score * 0.15
        )

        per_metric[name] = {
            "window_sum": round(weekly_sum, 3),
            "monthly_sum": round(monthly_sum, 3),
            "monthly_avg": round(monthly_avg, 3),
            "std": round(std, 6),
            "consistency": round(consistency, 6),
            "growth": None if gr is None else round(gr, 6),
            "goal": _sanitize_goal_info(goal_info),
            "metric_score": round(metric_score, 3),
            "intensity_score": round(intensity_score, 3),
            "stability_score": round(stability_score, 3),
            "growth_score": None if growth_score is None else round(growth_score, 3),
        }

        totals[name] = weekly_sum
        intensity_scores.append(intensity_score)
        consistency_scores.append(consistency * 100.0)
        if stability_score is not None:
            stability_scores.append(stability_score)
        if growth_score is not None:
            growth_scores.append(growth_score)

    # --- language-specific semantic indicators ---
    listen = totals.get("Listening Minutes", 0.0)
    speak = totals.get("Speaking Minutes", 0.0)
    read = totals.get("Reading Minutes", 0.0)
    write = totals.get("Writing Minutes", 0.0)
    vocab_new = totals.get("Vocabulary New", 0.0)
    vocab_review = totals.get("Vocabulary Review", 0.0)
    grammar = totals.get("Grammar Exercises", 0.0)

    study_intensity = listen + speak + read + write
    passive = (listen + read) or 1e-9
    active = speak + write
    active_passive_ratio = round((active / passive), 3) if passive > 0 else None

    skills = [listen, speak, read, write]
    mean_skill = statistics.mean(skills) if any(skills) else 0.0
    skill_std = statistics.pstdev(skills) if any(skills) else 0.0
    skill_balance_index = round(max(0.0, min(100.0, (1.0 - (skill_std / (mean_skill + 1e-9))) * 100.0)), 3) if mean_skill > 0 else 100.0  # ИСПРАВЛЕНИЕ 1: синтаксис

    vocab_rate = vocab_new
    vocab_retention = round((vocab_review / (vocab_new or 1.0)), 3) if vocab_new > 0 else 0.0

    total_study = study_intensity or 1e-9
    share_speaking = speak / total_study
    share_listening = listen / total_study
    share_reading = read / total_study
    share_writing = write / total_study
    fluency_score = round((0.4 * share_speaking + 0.2 * share_listening + 0.2 * share_reading + 0.2 * share_writing) * 100.0, 3)

    grammar_index = round((grammar / (study_intensity / 60.0 + 1e-9)) if study_intensity > 0 else 0.0, 3)
    grammar_score = round(min(100.0, grammar_index * 10.0), 3)

    recommendations = []
    if active_passive_ratio is None or active_passive_ratio < 0.5:
        recommendations.append("Увеличьте говорение/письмо (speaking/writing) — активная практика ускоряет прогресс.")
    if vocab_rate > 50:
        recommendations.append("Фокусируйтесь на повторении новых слов (spaced repetition) — уменьшите поток новых слов.")
    if vocab_retention < 0.5:
        recommendations.append("Добавьте больше ревью слов — это повысит удержание.")
    if skill_balance_index < 50:
        recommendations.append("Сбалансируйте время между навыками: listening, speaking, reading, writing.")
    if study_intensity < 60:
        recommendations.append("Нужна базовая интенсивность: попробуйте 1 час в неделю как минимум.")
    if not recommendations:
        recommendations.append("Хорошая динамика — поддерживайте последовательность и добавляйте активную практику.")

    # ИСПРАВЛЕНИЕ 4: взвешенная нормировка domain_score как в domain_basic_scores
    BASE_WEIGHTS = {
        "intensity":   0.35,
        "consistency": 0.25,
        "stability":   0.20,
        "growth":      0.10,
    }
    components: Dict[str, float] = {
        "intensity": _safe_mean(intensity_scores),
        "consistency": _safe_mean(consistency_scores),
    }
    if stability_scores:
        components["stability"] = _safe_mean(stability_scores)
    if growth_scores:
        components["growth"] = _safe_mean(growth_scores)

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
        "domain_score": domain_score,
    }

    specific_summary = {
        "study_intensity_minutes_week": round(study_intensity, 3),
        "active_passive_ratio": None if active_passive_ratio is None else round(active_passive_ratio, 3),
        "skill_balance_index": round(skill_balance_index, 3) if isinstance(skill_balance_index, float) else skill_balance_index,
        "vocab_rate_week": round(vocab_rate, 3),
        "vocab_retention": round(vocab_retention, 3),
        "fluency_score": float(fluency_score),
        "grammar_score": float(grammar_score),
        "recommendations": recommendations,
    }

    return {
        "domain_id": domain.pk,
        "domain_name": domain.name,
        "slug": getattr(domain, "slug", None),
        "period": {"type": period, "start": start_window.isoformat(), "end": end_window.isoformat()},
        "report": {"per_metric": per_metric, "summary": summary},
        "specific_summary": specific_summary,
    }