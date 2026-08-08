"""
life/services/ai/prompts.py

All prompt builders for the AI assistant.
Each function receives pre-built context dicts and returns a (system, user) tuple
ready to pass to the DeepSeek client.
"""

from datetime import date
from typing import Any


# ── helpers ───────────────────────────────────────────────────────────────────

def _today() -> str:
    return date.today().strftime("%B %d, %Y")


def _format_domain(name: str, data: dict) -> str:
    """Render a single domain report as readable text block."""
    lines = [f"  [{name.upper()}]"]

    summary = data.get("summary", {})
    if summary:
        score = summary.get("domain_score")
        if score is not None:
            lines.append(f"    Domain score: {score:.1f}/100")
        for k, v in summary.items():
            if k == "domain_score":
                continue
            if isinstance(v, (int, float)):
                lines.append(f"    {k}: {v:.1f}%")

    specific = data.get("specific_summary", {})
    if specific:
        lines.append("    Key metrics:")
        for k, v in specific.items():
            if k == "recommendations":
                continue
            if isinstance(v, (int, float)):
                lines.append(f"      {k}: {v:.2f}")
            elif isinstance(v, str):
                lines.append(f"      {k}: {v}")

    recs = specific.get("recommendations") or data.get("recommendations", [])
    if recs:
        lines.append("    Recommendations:")
        for r in recs[:3]:
            lines.append(f"      • {r}")

    return "\n".join(lines)


def _format_category(category: str, domains: dict[str, dict]) -> str:
    """Render all domains of a category."""
    lines = [f"=== {category.upper()} ==="]
    for name, data in domains.items():
        lines.append(_format_domain(name, data))
    return "\n".join(lines)


# ── base system prompt ────────────────────────────────────────────────────────

BASE_SYSTEM = """You are a personal life-tracking AI assistant. \
You analyze the user's habits, health, productivity and growth data \
collected across multiple life domains (language learning, sleep, stress, \
nutrition, fitness, programming, etc.).

Your role:
- Give honest, data-driven analysis — not generic advice
- Be specific: reference actual numbers and trends from the data
- Be concise but complete — use bullet points and short paragraphs
- Always end with 2-3 actionable recommendations based on the data
- Tone: direct, supportive, like a knowledgeable coach

Today's date: {today}
"""


# ── 1. weekly comparison ──────────────────────────────────────────────────────

def weekly_comparison_prompt(
    current_week: dict[str, Any],
    previous_week: dict[str, Any],
    current_range: str,
    previous_range: str,
) -> tuple[str, str]:
    """
    Compare current week vs previous week across all domains.
    
    current_week / previous_week: {domain_name: {summary, specific_summary}}
    current_range / previous_range: "Dec 09 – Dec 15"
    """
    system = BASE_SYSTEM.format(today=_today())

    current_block = "\n".join(
        _format_domain(name, data) for name, data in current_week.items()
    )
    previous_block = "\n".join(
        _format_domain(name, data) for name, data in previous_week.items()
    )

    user = f"""Compare my life metrics for the past two weeks.

CURRENT WEEK ({current_range}):
{current_block}

PREVIOUS WEEK ({previous_range}):
{previous_block}

Please provide:
1. Overall week-over-week comparison (what improved, what declined)
2. Top 3 wins this week
3. Top 2-3 areas that need attention
4. Specific actionable recommendations for next week
"""
    return system, user


# ── 2. monthly comparison ─────────────────────────────────────────────────────

def monthly_comparison_prompt(
    current_month: dict[str, Any],
    previous_month: dict[str, Any],
    current_label: str,
    previous_label: str,
) -> tuple[str, str]:
    """Compare current month vs previous month."""
    system = BASE_SYSTEM.format(today=_today())

    current_block = "\n".join(
        _format_domain(name, data) for name, data in current_month.items()
    )
    previous_block = "\n".join(
        _format_domain(name, data) for name, data in previous_month.items()
    )

    user = f"""Analyze my monthly progress.

{current_label.upper()}:
{current_block}

{previous_label.upper()} (previous):
{previous_block}

Please provide:
1. Monthly trend summary — overall direction (growth / plateau / decline)
2. Domain-by-domain highlights (brief, only notable changes)
3. Which habits are becoming consistent vs which are deteriorating
4. Strategic recommendations for next month
"""
    return system, user


# ── 3. category report ────────────────────────────────────────────────────────

CATEGORY_DESCRIPTIONS = {
    "mind":   "intellectual growth (language learning, programming, reading)",
    "body":   "physical health (fitness training, nutrition, body metrics)",
    "spirit": "mental wellbeing (sleep, stress management, daily habits)",
}

def category_report_prompt(
    category: str,
    domains_this_week: dict[str, Any],
    domains_last_week: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """
    In-depth report for a single category (mind / body / spirit).
    Optionally includes last week for comparison.
    """
    system = BASE_SYSTEM.format(today=_today())
    description = CATEGORY_DESCRIPTIONS.get(category.lower(), category)

    this_block = _format_category(category, domains_this_week)

    comparison_block = ""
    if domains_last_week:
        last_block = _format_category(f"{category} (last week)", domains_last_week)
        comparison_block = f"\nLAST WEEK FOR COMPARISON:\n{last_block}"

    user = f"""Give me a detailed report on my {category.upper()} category — {description}.

THIS WEEK:
{this_block}
{comparison_block}

Please provide:
1. Overall {category} health score and what's driving it
2. Strongest domain this week and why
3. Weakest domain and what's holding it back
4. Week-over-week change (if comparison data available)
5. 2-3 concrete actions to improve {category} next week
"""
    return system, user


# ── 4. domain deep dive ───────────────────────────────────────────────────────

def domain_deep_dive_prompt(
    domain_name: str,
    domain_slug: str,
    current_data: dict[str, Any],
    historical_data: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """
    Deep analysis of a single domain.
    historical_data: last 4 weeks aggregated or previous period.
    """
    system = BASE_SYSTEM.format(today=_today())

    current_block = _format_domain(domain_name, current_data)
    history_block = ""
    if historical_data:
        history_block = f"\nHISTORICAL CONTEXT (previous period):\n{_format_domain(domain_name, historical_data)}"

    per_metric = current_data.get("per_metric", {})
    metrics_block = ""
    if per_metric:
        metrics_block = "\nPER-METRIC BREAKDOWN:\n"
        for metric_name, mdata in per_metric.items():
            score = mdata.get("metric_score")
            growth = mdata.get("growth")
            consistency = mdata.get("consistency")
            streak = mdata.get("current_streak")
            parts = []
            if score is not None:     parts.append(f"score={score:.1f}")
            if growth is not None:    parts.append(f"growth={growth*100:+.1f}%")
            if consistency is not None: parts.append(f"consistency={consistency*100:.0f}%")
            if streak is not None:    parts.append(f"streak={streak}d")
            metrics_block += f"  {metric_name}: {', '.join(parts)}\n"

    user = f"""Give me a deep dive analysis of my {domain_name} domain.

CURRENT PERIOD:
{current_block}
{metrics_block}
{history_block}

Please provide:
1. Overall assessment — what does the data tell about my {domain_name} practice
2. Metric-by-metric insights (focus on outliers — unusually good or bad)
3. Consistency patterns — am I building a sustainable habit or volatile?
4. Growth trajectory — improving, stable, or declining?
5. Specific actionable steps to reach the next level in {domain_name}
"""
    return system, user


# ── 5. correlation analysis ───────────────────────────────────────────────────

def correlation_prompt(all_domains_data: dict[str, Any]) -> tuple[str, str]:
    """
    Analyze relationships between all domains.
    all_domains_data: {domain_name: {summary, specific_summary, per_metric}}
    """
    system = BASE_SYSTEM.format(today=_today())

    all_block = "\n".join(
        _format_domain(name, data) for name, data in all_domains_data.items()
    )

    user = f"""Analyze the relationships and correlations between my life domains.

ALL DOMAINS THIS PERIOD:
{all_block}

Please provide:
1. Key correlations you notice (e.g. "when stress is high, sleep quality drops")
2. Which domain appears to be the keystone habit — improving it likely lifts others
3. Which domains are in conflict (improving one may hurt another)
4. The biggest bottleneck across all domains right now
5. One high-leverage change that would positively ripple across multiple domains
"""
    return system, user


# ── 6. free question ──────────────────────────────────────────────────────────

def free_question_prompt(
    question: str,
    user_context: dict[str, Any],
) -> tuple[str, str]:
    """
    Answer a free-form user question using available context.
    user_context: all available domain data for current period.
    """
    system = BASE_SYSTEM.format(today=_today())

    context_block = "\n".join(
        _format_domain(name, data) for name, data in user_context.items()
    )

    user = f"""The user asks: "{question}"

Here is their current life tracking data for context:
{context_block}

Answer the question directly and specifically, using the data above where relevant.
If the question is not related to the tracking data, answer it as a knowledgeable coach.
"""
    return system, user


# ── 7. weekly auto-report (for Celery) ───────────────────────────────────────

def weekly_auto_report_prompt(
    all_domains_current: dict[str, Any],
    all_domains_previous: dict[str, Any],
    week_label: str,
) -> tuple[str, str]:
    """
    Used by Celery weekly task. Generates a structured report for all domains.
    Designed to be sent via Telegram.
    """
    system = BASE_SYSTEM.format(today=_today()) + \
        "\nFormat your response for Telegram: use *bold* for headers, " \
        "plain text for content, keep total length under 800 words."

    current_block = "\n".join(
        _format_domain(name, data) for name, data in all_domains_current.items()
    )
    previous_block = "\n".join(
        _format_domain(name, data) for name, data in all_domains_previous.items()
    )

    user = f"""Generate my weekly life report for {week_label}.

THIS WEEK:
{current_block}

PREVIOUS WEEK:
{previous_block}

Structure your report as:
*WEEK SUMMARY* — one sentence overall verdict
*MIND* — 2-3 sentences
*BODY* — 2-3 sentences  
*SPIRIT* — 2-3 sentences
*TOP WINS* — 3 bullet points
*NEEDS ATTENTION* — 2 bullet points
*FOCUS FOR NEXT WEEK* — 2-3 actionable priorities
"""
    return system, user


def monthly_auto_report_prompt(
    all_domains_current: dict[str, Any],
    all_domains_previous: dict[str, Any],
    month_label: str,
) -> tuple[str, str]:
    """
    Used by Celery monthly task. Generates a structured report for all domains.
    Designed to be sent via Telegram.
    """
    system = BASE_SYSTEM.format(today=_today()) + \
        "\nFormat your response for Telegram: use *bold* for headers, " \
        "plain text for content, keep total length under 800 words."

    current_block = "\n".join(
        _format_domain(name, data) for name, data in all_domains_current.items()
    )
    previous_block = "\n".join(
        _format_domain(name, data) for name, data in all_domains_previous.items()
    )

    user = f"""Generate my weekly life report for {month_label}.

THIS MONTH
{current_block}

PREVIOUS MONTH:
{previous_block}

Structure your report as:
*MONTH SUMMARY* — one sentence overall verdict
*MIND* — 2-3 sentences
*BODY* — 2-3 sentences  
*SPIRIT* — 2-3 sentences
*TOP WINS* — 3 bullet points
*NEEDS ATTENTION* — 2 bullet points
*FOCUS FOR NEXT MONTH* — 2-3 actionable priorities
"""
    return system, user


# ── 8. alert prompt (for Celery anomaly detection) ───────────────────────────

def alert_prompt(
    domain_name: str,
    metric_name: str,
    current_value: float,
    previous_value: float,
    change_pct: float,
    direction: str,  # "up" or "down"
    unit: str = "",
) -> tuple[str, str]:
    """
    Short alert when a metric changes significantly.
    Used by Celery to send Telegram notifications.
    """
    system = (
        "You are a concise life-tracking alert system. "
        "Write short, direct alerts (max 3 sentences). "
        "Be factual, not alarmist. Suggest one action."
    )

    direction_word = "increased" if direction == "up" else "dropped"
    user = (
        f"Alert: {metric_name} in {domain_name} has {direction_word} significantly.\n"
        f"Previous: {previous_value:.1f}{unit} → Current: {current_value:.1f}{unit} "
        f"({change_pct:+.1f}%).\n"
        f"Write a brief alert message and suggest one immediate action."
    )
    return system, user