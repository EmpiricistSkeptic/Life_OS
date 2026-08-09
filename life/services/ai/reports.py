"""
life/services/ai/reports.py

High-level report generation functions.
Ties together context.py + prompts.py + client.py.

Each function:
  1. Builds context via context.py
  2. Formats prompt via prompts.py
  3. Calls DeepSeek via client.py
  4. Returns result string
"""

import logging
from typing import Any

from .client import call_with_prompt, AIClientError
from .context import (
    get_weekly_comparison_context,
    get_monthly_comparison_context,
    get_category_comparison_context,
    get_domain_context,
    get_all_domains_context,
    get_weekly_context,
)
from .prompts import (
    weekly_comparison_prompt,
    monthly_comparison_prompt,
    category_report_prompt,
    domain_deep_dive_prompt,
    correlation_prompt,
    free_question_prompt,
    weekly_auto_report_prompt,
    monthly_auto_report_prompt,
    alert_prompt,

)
from life.models import Domain

logger = logging.getLogger(__name__)


# ── report type constants (used by AIAssistantView) ───────────────────────────

REPORT_WEEKLY_COMPARISON   = "weekly_comparison"
REPORT_MONTHLY_COMPARISON  = "monthly_comparison"
REPORT_CATEGORY            = "category"
REPORT_DOMAIN              = "domain"
REPORT_CORRELATIONS        = "correlations"
REPORT_FREE                = "free"

ALL_REPORT_TYPES = [
    REPORT_WEEKLY_COMPARISON,
    REPORT_MONTHLY_COMPARISON,
    REPORT_CATEGORY,
    REPORT_DOMAIN,
    REPORT_CORRELATIONS,
    REPORT_FREE,
]


# ── individual report functions ───────────────────────────────────────────────

def generate_weekly_comparison(user) -> str:
    """
    Compare this week vs last week across all domains.
    """
    logger.info(f"[AI] weekly_comparison for user={user.id}")
    ctx = get_weekly_comparison_context(user)
    prompt = weekly_comparison_prompt(
        current_week   = ctx["current"],
        previous_week  = ctx["previous"],
        current_range  = ctx["current_label"],
        previous_range = ctx["previous_label"],
    )
    return call_with_prompt(prompt, max_tokens=1200)


def generate_monthly_comparison(user) -> str:
    """
    Compare this month vs last month across all domains.
    """
    logger.info(f"[AI] monthly_comparison for user={user.id}")
    ctx = get_monthly_comparison_context(user)
    prompt = monthly_comparison_prompt(
        current_month  = ctx["current"],
        previous_month = ctx["previous"],
        current_label  = ctx["current_label"],
        previous_label = ctx["previous_label"],
    )
    return call_with_prompt(prompt, max_tokens=1400)


def generate_category_report(user, category: str) -> str:
    """
    In-depth report for a single category: mind / body / spirit.
    Includes week-over-week comparison.

    category: "mind" | "body" | "spirit"
    """
    category = category.lower().strip()
    if category not in ("mind", "body", "spirit"):
        raise ValueError(f"Unknown category: {category!r}. Use mind/body/spirit.")

    logger.info(f"[AI] category_report category={category} user={user.id}")
    ctx = get_category_comparison_context(user, category)
    prompt = category_report_prompt(
        category          = ctx["category"],
        domains_this_week = ctx["current"],
        domains_last_week = ctx["previous"],
    )
    return call_with_prompt(prompt, max_tokens=1000)


def generate_domain_deep_dive(user, domain_id: int, period: str = "weekly") -> str:
    """
    Deep analysis of a single domain with historical comparison.

    domain_id: Domain.id (must belong to user)
    period: "weekly" | "monthly"
    """

    try:
        domain = Domain.objects.get(id=domain_id, user=user)
    except Domain.DoesNotExist:
        raise ValueError(f"Domain {domain_id} not found for this user.")

    logger.info(f"[AI] domain_deep_dive domain={domain.name} user={user.id}")
    current, previous, label = get_domain_context(
        user, domain_id, period=period, include_previous=True
    )
    prompt = domain_deep_dive_prompt(
        domain_name   = domain.name,
        domain_slug   = domain.slug or "default",
        current_data  = current,
        historical_data = previous,
    )
    return call_with_prompt(prompt, max_tokens=1200)


def generate_correlation_analysis(user, period: str = "weekly") -> str:
    """
    Analyze relationships and correlations between all domains.
    """
    logger.info(f"[AI] correlation_analysis user={user.id}")
    all_data = get_all_domains_context(user, period=period)

    if not all_data:
        raise AIClientError("No domain data available for correlation analysis.")

    prompt = correlation_prompt(all_data)
    return call_with_prompt(prompt, max_tokens=1000, temperature=0.6)


def generate_free_answer(user, question: str, period: str = "weekly") -> str:
    """
    Answer a free-form user question using current period context.
    """
    if not question or not question.strip():
        raise ValueError("Question cannot be empty.")

    logger.info(f"[AI] free_answer user={user.id} question={question[:60]!r}")
    context_data, _ = get_weekly_context(user, offset=0)
    prompt = free_question_prompt(
        question     = question.strip(),
        user_context = context_data,
    )
    return call_with_prompt(prompt, max_tokens=800, temperature=0.8)


# ── router: called by AIAssistantView ─────────────────────────────────────────

def generate_report(user, report_type: str, payload: dict[str, Any]) -> str:
    """
    Main entry point called by AIAssistantView.
    Routes to the correct generator based on report_type.

    payload keys by type:
        weekly_comparison  → {}
        monthly_comparison → {}
        category           → {"category": "mind"|"body"|"spirit"}
        domain             → {"domain_id": int, "period": "weekly"|"monthly"}
        correlations       → {"period": "weekly"|"monthly"}
        free               → {"question": str}

    Returns response text string.
    Raises ValueError for bad input, AIClientError for API failures.
    """
    if report_type == REPORT_WEEKLY_COMPARISON:
        return generate_weekly_comparison(user)

    if report_type == REPORT_MONTHLY_COMPARISON:
        return generate_monthly_comparison(user)

    if report_type == REPORT_CATEGORY:
        category = payload.get("category")
        if not category:
            raise ValueError("'category' field required for category report.")
        return generate_category_report(user, category)

    if report_type == REPORT_DOMAIN:
        domain_id = payload.get("domain_id")
        if not domain_id:
            raise ValueError("'domain_id' field required for domain deep dive.")
        period = payload.get("period", "weekly")
        return generate_domain_deep_dive(user, int(domain_id), period=period)

    if report_type == REPORT_CORRELATIONS:
        period = payload.get("period", "weekly")
        return generate_correlation_analysis(user, period=period)

    if report_type == REPORT_FREE:
        question = payload.get("question")
        if not question:
            raise ValueError("'question' field required for free answer.")
        return generate_free_answer(user, question)

    raise ValueError(
        f"Unknown report_type: {report_type!r}. "
        f"Valid types: {ALL_REPORT_TYPES}"
    )


# ── celery tasks helpers (called from tasks.py) ───────────────────────────────

def generate_weekly_auto_report(user) -> str:
    """
    Used by Celery weekly task.
    Generates a Telegram-formatted weekly report.
    """
    logger.info(f"[AI] weekly_auto_report for user={user.id}")
    ctx = get_weekly_comparison_context(user)

    if not ctx["current"]:
        return "No data available for weekly report."

    prompt = weekly_auto_report_prompt(
        all_domains_current  = ctx["current"],
        all_domains_previous = ctx["previous"],
        week_label           = ctx["current_label"],
    )
    return call_with_prompt(prompt, max_tokens=900, temperature=0.6)


def generate_monthly_auto_report(user) -> str:

    logger.info(f"[AI] monthly_auto_report for user={user.id}")

    ctx = get_monthly_comparison_context(user)
    if not ctx["current"]:
        return "No data available for monthly report."
    
    prompt = monthly_auto_report_prompt(
        all_domains_current = ctx["current"],
        all_domains_previous = ctx["previous"],
        month_label = ctx["current_label"],
    )
    return call_with_prompt(prompt, max_tokens=1200, temperature=0.6)



def generate_metric_alert(
    user,
    domain_name: str,
    metric_name: str,
    current_value: float,
    previous_value: float,
    unit: str = "",
) -> str:
    """
    Used by Celery anomaly detection task.
    Generates a short alert message for a metric that changed significantly.
    """
    if previous_value == 0:
        change_pct = 100.0
    else:
        change_pct = ((current_value - previous_value) / abs(previous_value)) * 100

    direction = "up" if current_value > previous_value else "down"

    logger.info(
        f"[AI] alert domain={domain_name} metric={metric_name} "
        f"change={change_pct:+.1f}%"
    )
    prompt = alert_prompt(
        domain_name    = domain_name,
        metric_name    = metric_name,
        current_value  = current_value,
        previous_value = previous_value,
        change_pct     = change_pct,
        direction      = direction,
        unit           = unit,
    )
    return call_with_prompt(prompt, max_tokens=150, temperature=0.5)