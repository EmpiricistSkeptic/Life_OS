"""
life/services/ai/context.py

Builds context dictionaries from existing domain services.
These are passed directly to prompt functions in prompts.py.

All heavy lifting (calculations) is already done in:
  - life/services/domains/<slug>.py  (specific analytics)
  - life/services/domains/generic.py (generic analytics)
  - life/services/domains/registry.py (dynamic loader)
"""

from datetime import date, timedelta
from typing import Any

from life.models import Domain
from life.services.domains import registry
from life.services.domains.generic import get_domain_report


# ── date helpers ──────────────────────────────────────────────────────────────

def _week_range(offset: int = 0) -> tuple[date, date]:
    """
    Returns (start, end) for a week.
    offset=0 → current week (Mon–Sun)
    offset=-1 → previous week
    """
    today = date.today()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
    sunday = monday + timedelta(days=6)
    return monday, sunday


def _month_range(offset: int = 0) -> tuple[date, date]:
    """
    Returns (start, end) for a calendar month.
    offset=0 → current month
    offset=-1 → previous month
    """
    today = date.today()
    # first day of current month
    first = today.replace(day=1)
    if offset < 0:
        for _ in range(abs(offset)):
            first = (first - timedelta(days=1)).replace(day=1)
    # last day of that month
    next_month = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    last = next_month - timedelta(days=1)
    return first, last


def _fmt_range(start: date, end: date) -> str:
    return f"{start.strftime('%b %d')} – {end.strftime('%b %d, %Y')}"


def _fmt_month(start: date) -> str:
    return start.strftime("%B %Y")


# ── core: run one domain service ──────────────────────────────────────────────

def _run_domain_service(
    domain: Domain,
    user,
    end_date: date,
    period: str = "weekly",
) -> dict[str, Any] | None:
    """
    Runs the appropriate analytics service for a domain.
    Returns the report dict or None if service unavailable.
    """
    try:
        service = registry.get_service(domain.slug)
        result = service(domain, user, end_date=end_date, period=period)
        # result is a dict with keys: summary, per_metric, specific_summary, etc.
        return result
    except Exception:
        # if no specific service exists (e.g. slug=default), try generic
        try:
            result = get_domain_report(domain, user, end_date=end_date, period=period)
            return result
        except Exception:
            return None


# ── build context for a set of domains ───────────────────────────────────────

def _build_domains_context(
    domains,
    user,
    end_date: date,
    period: str = "weekly",
) -> dict[str, dict]:
    """
    Runs services for a list of Domain objects.
    Returns {domain.name: report_dict}
    """
    context = {}
    for domain in domains:
        report = _run_domain_service(domain, user, end_date=end_date, period=period)
        if report is not None:
            context[domain.name] = report
    return context


# ── public API ────────────────────────────────────────────────────────────────

def get_weekly_context(
    user,
    offset: int = 0,
) -> tuple[dict[str, dict], str]:
    """
    Returns context for all user domains for a given week.

    offset=0  → current week
    offset=-1 → previous week

    Returns:
        (domains_data, range_label)
        domains_data: {domain_name: report_dict}
        range_label:  "Dec 09 – Dec 15, 2024"
    """
    start, end = _week_range(offset)
    domains = Domain.objects.filter(user=user).select_related("category")
    data = _build_domains_context(domains, user, end_date=end, period="weekly")
    return data, _fmt_range(start, end)


def get_monthly_context(
    user,
    offset: int = 0,
) -> tuple[dict[str, dict], str]:
    """
    Returns context for all user domains for a given month.

    offset=0  → current month
    offset=-1 → previous month
    """
    start, end = _month_range(offset)
    domains = Domain.objects.filter(user=user).select_related("category")
    data = _build_domains_context(domains, user, end_date=end, period="monthly")
    return data, _fmt_month(start)


def get_category_context(
    user,
    category_slug: str,      # "mind" | "body" | "spirit"
    offset: int = 0,
    period: str = "weekly",
) -> tuple[dict[str, dict], str]:
    """
    Returns context for all domains belonging to a specific category.

    category_slug: "mind", "body", or "spirit"
    offset=0 → current period, offset=-1 → previous period
    """
    if period == "weekly":
        start, end = _week_range(offset)
        label = _fmt_range(start, end)
    else:
        start, end = _month_range(offset)
        label = _fmt_month(start)

    domains = Domain.objects.filter(
        user=user,
        category__name__iexact=category_slug,
    ).select_related("category")

    data = _build_domains_context(domains, user, end_date=end, period=period)
    return data, label


def get_domain_context(
    user,
    domain_id: int,
    period: str = "weekly",
    include_previous: bool = True,
) -> tuple[dict, dict | None, str]:
    """
    Returns context for a single domain, optionally with previous period.

    Returns:
        (current_data, previous_data, label)
        previous_data is None if include_previous=False
    """
    domain = Domain.objects.get(id=domain_id, user=user)

    if period == "weekly":
        _, end_current  = _week_range(0)
        _, end_previous = _week_range(-1)
        label = _fmt_range(*_week_range(0))
    else:
        _, end_current  = _month_range(0)
        _, end_previous = _month_range(-1)
        label = _fmt_month(_month_range(0)[0])

    current = _run_domain_service(domain, user, end_date=end_current, period=period)
    previous = None
    if include_previous:
        previous = _run_domain_service(domain, user, end_date=end_previous, period=period)

    return current or {}, previous, label


def get_all_domains_context(
    user,
    period: str = "weekly",
    offset: int = 0,
) -> dict[str, dict]:
    """
    Returns context for ALL user domains — used for correlation analysis.
    """
    if period == "weekly":
        _, end = _week_range(offset)
    else:
        _, end = _month_range(offset)

    domains = Domain.objects.filter(user=user).select_related("category")
    return _build_domains_context(domains, user, end_date=end, period=period)


def get_weekly_comparison_context(user) -> dict:
    """
    Convenience function: returns both current and previous week contexts.
    Used by weekly_comparison_prompt and weekly_auto_report_prompt.

    Returns:
    {
        "current":        {domain_name: report},
        "previous":       {domain_name: report},
        "current_label":  "Dec 09 – Dec 15, 2024",
        "previous_label": "Dec 02 – Dec 08, 2024",
    }
    """
    current_data,  current_label  = get_weekly_context(user, offset=0)
    previous_data, previous_label = get_weekly_context(user, offset=-1)
    return {
        "current":        current_data,
        "previous":       previous_data,
        "current_label":  current_label,
        "previous_label": previous_label,
    }


def get_monthly_comparison_context(user) -> dict:
    """
    Convenience function: returns both current and previous month contexts.

    Returns:
    {
        "current":        {domain_name: report},
        "previous":       {domain_name: report},
        "current_label":  "December 2024",
        "previous_label": "November 2024",
    }
    """
    current_data,  current_label  = get_monthly_context(user, offset=0)
    previous_data, previous_label = get_monthly_context(user, offset=-1)
    return {
        "current":        current_data,
        "previous":       previous_data,
        "current_label":  current_label,
        "previous_label": previous_label,
    }


def get_category_comparison_context(user, category_slug: str) -> dict:
    """
    Current week vs previous week for a single category.
    Used by category_report_prompt.
    """
    current_data,  label   = get_category_context(user, category_slug, offset=0)
    previous_data, _       = get_category_context(user, category_slug, offset=-1)
    return {
        "category":  category_slug,
        "current":   current_data,
        "previous":  previous_data,
        "label":     label,
    }